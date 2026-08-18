import os
import re
import tempfile
import subprocess
import wave
import hashlib

from flask import Flask, request, send_file, jsonify, render_template
from PIL import Image

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500MB uploads

STEGO_BIN = os.path.abspath("./stego")

# =========================================================
# IMAGE STEGO (uses your existing ./stego binary)
# =========================================================

def run_stego_encode(in_img_path: str, message: str, password: str, out_img_path: str):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as tf:
        tf.write(message)
        txt_path = tf.name

    try:
        # encode prompts: password + confirm password
        stdin_payload = f"{password}\n{password}\n" if password else "\n\n"
        p = subprocess.run(
            [STEGO_BIN, "encode", in_img_path, txt_path, out_img_path],
            input=stdin_payload,
            text=True,
            capture_output=True,
            check=False,
        )
        out = (p.stdout + "\n" + p.stderr).strip()

        if p.returncode != 0 or not os.path.exists(out_img_path):
            return False, out or "Encode failed."
        return True, "OK"
    finally:
        try:
            os.remove(txt_path)
        except OSError:
            pass


def run_stego_decode(enc_img_path: str, password: str):
    stdin_payload = f"{password}\n" if password else "\n"
    p = subprocess.run(
        [STEGO_BIN, "decode", enc_img_path],
        input=stdin_payload,
        text=True,
        capture_output=True,
        check=False,
    )
    out = (p.stdout + "\n" + p.stderr).strip()

    m = re.search(r"Decrypted Message:\s*(.*)", out)
    if not m:
        return False, out or "Decode failed."
    return True, m.group(1)


# =========================================================
# SHARED HELPERS (XOR + bit conversion)
# =========================================================

def kdf(password: str) -> bytes:
    return hashlib.sha256(password.encode("utf-8")).digest()

def xor_bytes(data: bytes, key: bytes) -> bytes:
    if not key:
        return data
    out = bytearray(len(data))
    for i, b in enumerate(data):
        out[i] = b ^ key[i % len(key)]
    return bytes(out)

def to_bits(data: bytes):
    for b in data:
        for i in range(7, -1, -1):
            yield (b >> i) & 1

def from_bits(bits):
    out = bytearray()
    cur = 0
    n = 0
    for bit in bits:
        cur = (cur << 1) | bit
        n += 1
        if n == 8:
            out.append(cur)
            cur = 0
            n = 0
    return bytes(out)


# =========================================================
# AUDIO STEGO (WAV LSB)
# =========================================================

AUDIO_MAGIC = b"STEG0A"

def audio_encode_wav(in_wav: str, message: str, password: str, out_wav: str):
    with wave.open(in_wav, "rb") as w:
        params = w.getparams()
        frames = bytearray(w.readframes(w.getnframes()))

    key = kdf(password) if password else b""
    enc_msg = xor_bytes(message.encode("utf-8"), key)
    payload = AUDIO_MAGIC + len(enc_msg).to_bytes(4, "big") + enc_msg

    needed_bits = len(payload) * 8
    capacity_bits = len(frames)  # 1 bit per byte
    if needed_bits > capacity_bits:
        raise ValueError(f"Message too large for this WAV. Need {needed_bits} bits, have {capacity_bits} bits.")

    bit_iter = to_bits(payload)
    for i in range(needed_bits):
        frames[i] = (frames[i] & 0xFE) | next(bit_iter)

    with wave.open(out_wav, "wb") as w:
        w.setparams(params)
        w.writeframes(bytes(frames))

def audio_decode_wav(in_wav: str, password: str) -> str:
    with wave.open(in_wav, "rb") as w:
        frames = w.readframes(w.getnframes())

    header_len = len(AUDIO_MAGIC) + 4
    header_bits = [(frames[i] & 1) for i in range(header_len * 8)]
    header = from_bits(header_bits)

    if not header.startswith(AUDIO_MAGIC):
        raise ValueError("No hidden audio payload found (bad magic).")

    msg_len = int.from_bytes(header[len(AUDIO_MAGIC):len(AUDIO_MAGIC)+4], "big")
    total_bits = (header_len + msg_len) * 8

    bits = [(frames[i] & 1) for i in range(total_bits)]
    payload = from_bits(bits)

    enc_msg = payload[header_len:header_len+msg_len]
    key = kdf(password) if password else b""
    msg = xor_bytes(enc_msg, key).decode("utf-8", errors="replace")
    return msg


# =========================================================
# VIDEO STEGO (FULL LENGTH, AVI only, FFV1 lossless)
# Key fix: extract frames as rgb24 to avoid bgr0 issues
# =========================================================

VIDEO_MAGIC = b"STEG0V"
SCAN_FRAMES = 80  # decode scans first N frames

def ffprobe_fps(video_path: str) -> str:
    p = subprocess.run(
        ["ffprobe", "-v", "error",
         "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate",
         "-of", "default=noprint_wrappers=1:nokey=1",
         video_path],
        capture_output=True, text=True, check=False
    )
    fps = (p.stdout or "").strip()
    return fps if fps else "25"

def extract_all_frames_rgb24(video_path: str, outdir: str):
    # format=rgb24 ensures consistent channel layout for LSB
    subprocess.run(
        ["ffmpeg", "-y",
         "-i", video_path,
         "-vf", "format=rgb24",
         os.path.join(outdir, "%06d.png")],
        capture_output=True, check=True, text=True
    )

def extract_n_frames_rgb24(video_path: str, outdir: str, n: int):
    subprocess.run(
        ["ffmpeg", "-y",
         "-i", video_path,
         "-vf", f"select='lt(n\\,{n})',format=rgb24",
         "-vsync", "0",
         os.path.join(outdir, "%06d.png")],
        capture_output=True, check=True, text=True
    )

def rebuild_avi_ffv1_with_audio(frames_dir: str, fps: str, original_video: str, out_avi: str):
    # AVI only + FFV1 lossless preserves LSB
    subprocess.run(
        ["ffmpeg", "-y",
         "-framerate", fps,
         "-i", os.path.join(frames_dir, "%06d.png"),
         "-i", original_video,
         "-map", "0:v:0",
         "-map", "1:a?",
         "-c:v", "ffv1",
         "-level", "3",
         "-g", "1",
         "-c:a", "copy",
         "-shortest",
         out_avi],
        capture_output=True, check=True, text=True
    )

def embed_payload_into_png(in_png: str, payload: bytes, out_png: str):
    img = Image.open(in_png).convert("RGB")
    px = img.load()

    bits = list(to_bits(payload))
    capacity = img.width * img.height * 3
    if len(bits) > capacity:
        raise ValueError(f"Payload too large for frame. Need {len(bits)} bits, have {capacity} bits.")

    i = 0
    for y in range(img.height):
        for x in range(img.width):
            r, g, b = px[x, y]
            if i < len(bits): r = (r & 0xFE) | bits[i]; i += 1
            if i < len(bits): g = (g & 0xFE) | bits[i]; i += 1
            if i < len(bits): b = (b & 0xFE) | bits[i]; i += 1
            px[x, y] = (r, g, b)
            if i >= len(bits):
                img.save(out_png, "PNG")
                return

    raise RuntimeError("Embedding failed unexpectedly.")

def try_decode_payload_from_png(png_path: str, password: str) -> str:
    img = Image.open(png_path).convert("RGB")
    px = img.load()

    header_len = len(VIDEO_MAGIC) + 4
    header_bits_len = header_len * 8

    bits = []
    # read bits progressively, get header, then collect full payload bits
    for y in range(img.height):
        for x in range(img.width):
            r, g, b = px[x, y]
            bits.extend([r & 1, g & 1, b & 1])

            if len(bits) >= header_bits_len:
                header = from_bits(bits[:header_bits_len])
                if not header.startswith(VIDEO_MAGIC):
                    raise ValueError("bad magic")

                msg_len = int.from_bytes(header[len(VIDEO_MAGIC):len(VIDEO_MAGIC)+4], "big")
                total_bits_needed = (header_len + msg_len) * 8

                # continue collecting until total bits satisfied
                if len(bits) < total_bits_needed:
                    for yy in range(y, img.height):
                        xx_start = x + 1 if yy == y else 0
                        for xx in range(xx_start, img.width):
                            rr, gg, bb = px[xx, yy]
                            bits.extend([rr & 1, gg & 1, bb & 1])
                            if len(bits) >= total_bits_needed:
                                break
                        if len(bits) >= total_bits_needed:
                            break

                if len(bits) < total_bits_needed:
                    raise ValueError("corrupt payload")

                payload = from_bits(bits[:total_bits_needed])
                enc_msg = payload[header_len:header_len+msg_len]

                key = kdf(password) if password else b""
                msg = xor_bytes(enc_msg, key).decode("utf-8", errors="replace")
                return msg

    raise ValueError("no payload")


# =========================================================
# ROUTES
# =========================================================

@app.get("/")
def home():
    return render_template("index.html")


# Image
@app.post("/encode")
def img_encode():
    image = request.files.get("image")
    message = request.form.get("message", "")
    password = request.form.get("password", "")

    if not image:
        return jsonify({"error": "No image uploaded"}), 400
    if not message.strip():
        return jsonify({"error": "Message is empty"}), 400

    with tempfile.TemporaryDirectory() as td:
        in_img = os.path.join(td, "in.png")
        out_img = os.path.join(td, "embedded.png")
        image.save(in_img)

        ok, info = run_stego_encode(in_img, message, password, out_img)
        if not ok:
            return jsonify({"error": "Encode failed", "details": info}), 400

        return send_file(out_img, mimetype="image/png", as_attachment=True, download_name="embedded.png")


@app.post("/decode")
def img_decode():
    image = request.files.get("image")
    password = request.form.get("password", "")

    if not image:
        return jsonify({"error": "No image uploaded"}), 400

    with tempfile.TemporaryDirectory() as td:
        in_img = os.path.join(td, "in.png")
        image.save(in_img)

        ok, info = run_stego_decode(in_img, password)
        if not ok:
            return jsonify({"error": "Decode failed", "details": info}), 400

        return jsonify({"message": info})


# Audio
@app.post("/audio/encode")
def aud_encode():
    audio = request.files.get("audio")
    message = request.form.get("message", "")
    password = request.form.get("password", "")

    if not audio:
        return jsonify({"error": "No WAV uploaded"}), 400
    if not message.strip():
        return jsonify({"error": "Message is empty"}), 400

    with tempfile.TemporaryDirectory() as td:
        in_wav = os.path.join(td, "in.wav")
        out_wav = os.path.join(td, "embedded.wav")
        audio.save(in_wav)

        try:
            audio_encode_wav(in_wav, message, password, out_wav)
        except Exception as e:
            return jsonify({"error": "Audio encode failed", "details": str(e)}), 400

        return send_file(out_wav, mimetype="audio/wav", as_attachment=True, download_name="embedded.wav")


@app.post("/audio/decode")
def aud_decode():
    audio = request.files.get("audio")
    password = request.form.get("password", "")

    if not audio:
        return jsonify({"error": "No WAV uploaded"}), 400

    with tempfile.TemporaryDirectory() as td:
        in_wav = os.path.join(td, "in.wav")
        audio.save(in_wav)

        try:
            msg = audio_decode_wav(in_wav, password)
        except Exception as e:
            return jsonify({"error": "Audio decode failed", "details": str(e)}), 400

        return jsonify({"message": msg})


# Video (FULL LENGTH, AVI only)
@app.post("/video/encode")
def vid_encode():
    video = request.files.get("video")
    message = request.form.get("message", "")
    password = request.form.get("password", "")

    if not video:
        return jsonify({"error": "No video uploaded"}), 400
    if not message.strip():
        return jsonify({"error": "Message is empty"}), 400

    with tempfile.TemporaryDirectory() as td:
        in_vid = os.path.join(td, "input_video")
        frames_dir = os.path.join(td, "frames")
        os.makedirs(frames_dir, exist_ok=True)
        out_avi = os.path.join(td, "embedded.avi")

        video.save(in_vid)

        try:
            fps = ffprobe_fps(in_vid)

            # Extract ALL frames as rgb24 PNGs (critical)
            extract_all_frames_rgb24(in_vid, frames_dir)

            first_frame = os.path.join(frames_dir, "000001.png")
            if not os.path.exists(first_frame):
                return jsonify({"error": "Failed to extract frames from video."}), 400

            key = kdf(password) if password else b""
            enc_msg = xor_bytes(message.encode("utf-8"), key)

            payload = VIDEO_MAGIC + len(enc_msg).to_bytes(4, "big") + enc_msg

            tmp_frame = os.path.join(td, "first_embedded.png")
            embed_payload_into_png(first_frame, payload, tmp_frame)

            # overwrite first frame
            Image.open(tmp_frame).save(first_frame, "PNG")

            # rebuild full-length AVI FFV1 lossless, copy audio if present
            rebuild_avi_ffv1_with_audio(frames_dir, fps, in_vid, out_avi)

        except subprocess.CalledProcessError as e:
            details = (e.stderr or e.stdout or str(e))
            return jsonify({"error": "FFmpeg failed", "details": details}), 400
        except Exception as e:
            return jsonify({"error": "Video encode failed", "details": str(e)}), 400

        return send_file(out_avi, mimetype="video/x-msvideo", as_attachment=True, download_name="embedded.avi")


@app.post("/video/decode")
def vid_decode():
    video = request.files.get("video")
    password = request.form.get("password", "")

    if not video:
        return jsonify({"error": "No video uploaded"}), 400

    with tempfile.TemporaryDirectory() as td:
        in_vid = os.path.join(td, "input_video")
        frames_dir = os.path.join(td, "frames")
        os.makedirs(frames_dir, exist_ok=True)

        video.save(in_vid)

        try:
            # Extract first N frames as rgb24 PNGs (fast + reliable)
            extract_n_frames_rgb24(in_vid, frames_dir, SCAN_FRAMES)

            for i in range(1, SCAN_FRAMES + 1):
                frame = os.path.join(frames_dir, f"{i:06d}.png")
                if not os.path.exists(frame):
                    continue
                try:
                    msg = try_decode_payload_from_png(frame, password)
                    return jsonify({"message": msg})
                except Exception:
                    pass

            return jsonify({
                "error": "Decode failed",
                "details": "No hidden video payload found in scanned frames. Make sure you decode the exact embedded.avi output (FFV1) and not a re-encoded copy."
            }), 400

        except subprocess.CalledProcessError as e:
            details = (e.stderr or e.stdout or str(e))
            return jsonify({"error": "FFmpeg failed", "details": details}), 400
        except Exception as e:
            return jsonify({"error": "Server error", "details": str(e)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
