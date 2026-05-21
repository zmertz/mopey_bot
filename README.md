# Mopey Bot
A Discord bot that plays music from YouTube and Plex.

## Prerequisites
Install system dependencies before running:
```bash
sudo apt install ffmpeg nodejs -y
```

## Configuration
Create a `.env` file in the root directory with the following variables:
```
discord_token=your_token_here
```

For Plex support, also add:
```
plex_base_url=your_plex_url
plex_token=your_plex_token
```

## Installation
Set up a virtual environment and install dependencies:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Usage
```bash
python3 main.py
```