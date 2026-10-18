<p align="center">
  <img src="ui/assets/nod-logo.png" alt="Nod Logo" width="300">
</p>

# Nod

### Hands-free web browsing for everyone.

Nod is an accessible, software-only Chromium browser environment built for the Technica Hackathon 2026. It replaces typing, mouse navigation, and manual scrolling with head movements, local voice cues, and accessible keyboard fallbacks.

All camera and microphone processing happens locally on your computer. No audio or video data is ever sent to cloud services.

---

## Why We Built It

Being able to use the internet should not be a luxury.

For people with motor limitations or conditions like arthritis, cerebral palsy, ALS, or spinal cord injuries, operating a standard mouse and keyboard can be exhausting or impossible. Specialized accessibility hardware often costs thousands of dollars, putting it out of reach for many.

Nod provides a free, lightweight alternative that runs on standard consumer computers using everyday webcams and microphones, restoring independent access to browse, read, pay bills, and communicate.

---

## What It Does

- **Head Navigation**: Turn your head right or left to move focus across links, buttons, and form controls.
- **Head Scrolling**: Tilt your head up or down to scroll web pages smoothly. Hold your head tilted (ear to shoulder) to toggle scroll mode on or off.
- **Voice Clicks**: Hold a steady **"ooo"** sound (~0.5s) to left-click the focused item, or **"aaa"** to right-click.
- **Backup Testing Keys**: Press `;` (semicolon) in the browser to left-click, or `'` (apostrophe) to right-click whatever is currently focused.
- **AI Image Detection**: A passive background scanner highlights suspected AI-generated images on visited pages with an indicator banner.
- **Practice Lab**: Built-in interactive demo pages to test controls and get comfortable with hands-free navigation.

---

## How It Works

Nod pairs a Python controller with Playwright to manage a live Chromium browser session:

- **Local Head Tracking**: MediaPipe analyzes webcam frames locally to estimate 3D head orientation (yaw, pitch, roll) in real time.
- **Local Audio Analysis**: Python analyzes microphone audio streams locally to recognize sustained vowel sounds without cloud speech APIs.
- **Clean Page Integration**: Injected scripts handle focus indicators, tab navigation, and shortcut triggers across pages and frames while keeping raw sensor data private and isolated from the web.

---

## Getting Started

### Prerequisites

- Python 3.10+
- A standard webcam and microphone

### Running Nod

```sh
# Clone the repository
git clone https://github.com/Yeti-Owner/technica-hackathon-2026.git
cd technica-hackathon-2026

# Standard mode (voice clicks + keyboard navigation)
python run.py

# Hands-free mode (camera head tracking + voice clicks)
python run.py --head
```

On first launch, `run.py` automatically sets up a local virtual environment (`.venv`), installs required dependencies, and downloads the Chromium browser.

To use the optional AI image detection feature, copy `.env.example` to `.env` and fill in your server URL and API key.

---

## The Team

Built by three students for the Technica Hackathon 2026:

- **Tomas Karounas** — Computer Science, University of North Georgia
- **Callum Maclean** — Computer Science, University of North Georgia
- **Nick McDowell** — Cybersecurity, Georgia Tech
