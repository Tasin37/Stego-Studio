# Stego Studio

> Local web app for hiding and extracting text inside images, WAV audio, and video files.

Supports three steganography modes:

- **Image:** uses an external `./stego` binary (PNG/JPG input → embedded PNG output).
- **Audio:** LSB in WAV (PCM) files (WAV input → embedded WAV output).
- **Video:** embeds into the first frame and rebuilds a lossless AVI (use the exact generated AVI for reliable decoding).

## Features

- Image encode/decode via external `stego` tool (password optional)
- Audio encode/decode using WAV LSB + optional password (XOR + SHA256 KDF)
- Video encode/decode by embedding payload into the first RGB frame and rebuilding an AVI (FFV1 lossless)
- Simple browser UI at `/` to interact with all modes

## Requirements

- Python 3.8+
- pip
- `ffmpeg` and `ffprobe` available on your PATH (used for video processing)
- An executable `stego` binary placed in the project root for image encode/decode (see notes)
- Python packages: `Flask`, `Pillow`

Recommended (Linux/macOS):

```bash
sudo apt install ffmpeg    # Debian/Ubuntu
brew install ffmpeg       # macOS (Homebrew)
```

Install Python deps:

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install Flask Pillow
```

If you prefer a single install command:

```bash
pip install Flask Pillow
```

## Project layout

- `app.py` - Flask application and core stego handlers
- `templates/index.html` - Browser UI
- `stego` - (expected) external binary used for image stego operations

If you don't have a `stego` binary, place your compiled executable at the repository root named `stego`. On Windows you can use `stego.exe` and update `STEGO_BIN` in `app.py` to point to it.

## Running the app

1. Ensure dependencies are installed and `ffmpeg`/`ffprobe` are available.
2. Ensure the `stego` binary is present at the project root if you want image encode/decode functionality.
3. Start the server:

```bash
python app.py
```

Open http://127.0.0.1:5000 in your browser to use the UI.

### Windows (VirtualBox + Kali Linux)

If you're running the project from Windows but prefer to run the server inside a Kali Linux VM in VirtualBox, create a shared folder that maps the project directory into the VM and run the app from the shared folder.

Steps inside Kali (example):

1. Open a terminal in Kali.
2. Mount the shared folder (if not auto-mounted). Replace `StegoShared` with the name you configured in VirtualBox and `~/Stego-Studio-main` with your desired mount point:

```bash
sudo mkdir -p ~/Stego-Studio-main
sudo mount -t vboxsf StegoShared ~/Stego-Studio-main
```

3. Change into the project directory and run the commands you provided:

```bash
cd ~/Stego-Studio-main
source venv/bin/activate    # or: source .venv/bin/activate if you used .venv
chmod +x stego
python app.py
```

4. Open the app:

- In the Kali VM browser use: http://127.0.0.1:5000
- To access from the Windows host, either set the Flask host to `0.0.0.0` and open `http://<vm-ip>:5000` from Windows, or configure VirtualBox NAT port forwarding for port 5000.

Note: networking between the VM and host depends on your VirtualBox network mode (NAT vs Bridged). Using Bridged or port forwarding makes the server reachable from the host.

## API Endpoints

All endpoints accept multipart form data.

Image:

- POST `/encode` - fields: `image` (file), `message` (text), `password` (optional)
- POST `/decode` - fields: `image` (file), `password` (optional)

Audio (WAV):

- POST `/audio/encode` - fields: `audio` (WAV file), `message` (text), `password` (optional)
- POST `/audio/decode` - fields: `audio` (WAV file), `password` (optional)

Video (AVI recommended output):

- POST `/video/encode` - fields: `video` (file), `message` (text), `password` (optional)
- POST `/video/decode` - fields: `video` (file), `password` (optional)

### Examples (curl)

Image encode (download produced file):

```bash
curl -F "image=@input.png" -F "message=Secret text" -F "password=optional" http://127.0.0.1:5000/encode -o embedded.png
```

Image decode:

```bash
curl -F "image=@embedded.png" -F "password=optional" http://127.0.0.1:5000/decode
```

Audio encode (WAV):

```bash
curl -F "audio=@input.wav" -F "message=Hello" -F "password=optional" http://127.0.0.1:5000/audio/encode -o embedded.wav
```

Audio decode:

```bash
curl -F "audio=@embedded.wav" -F "password=optional" http://127.0.0.1:5000/audio/decode
```

Video encode:

```bash
curl -F "video=@input.mp4" -F "message=Hidden" -F "password=optional" http://127.0.0.1:5000/video/encode -o embedded.avi
```

Video decode:

```bash
curl -F "video=@embedded.avi" -F "password=optional" http://127.0.0.1:5000/video/decode
```

## Notes & Troubleshooting

- Image operations require the external `stego` binary. If you see `Encode failed` or `Decode failed` from image endpoints, confirm `STEGO_BIN` in `app.py` points to a working executable and that it has execute permissions.
- Video embedding rebuilds an AVI with FFV1 lossless codec to preserve exact LSBs — decoding may fail if the file has been re-encoded with a lossy codec. Keep the original `embedded.avi` produced by this app for reliable decoding.
- Audio encoding expects enough capacity in the WAV file. Large messages may raise `Message too large for this WAV.`
- If `ffmpeg` or `ffprobe` are missing, video endpoints will return an error mentioning FFmpeg.

## Development

- The server is a single-file Flask app (`app.py`) and uses `templates/index.html` for the UI. Modify templates or routes to extend functionality.
- Tests are not included; you can exercise endpoints via the UI or curl commands above.

