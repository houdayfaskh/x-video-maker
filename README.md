# Tweet Video Maker

Outil web qui prend un lien de tweet (Twitter/X) contenant une vidéo, extrait la vidéo et le texte, puis génère une nouvelle vidéo avec le texte du tweet affiché sur un bandeau noir en haut.

## Prérequis

- **Python 3.10+**
- **FFmpeg** installé et accessible dans le PATH
- **yt-dlp** (installé via pip)

### Installer FFmpeg (macOS)

```bash
brew install ffmpeg
```

## Installation

```bash
cd app_tracker_d\'habitudes
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Lancement

```bash
source venv/bin/activate
python app.py
```

Ouvrir [http://localhost:5000](http://localhost:5000) dans le navigateur.

## Utilisation

1. Coller un lien de tweet contenant une vidéo (twitter.com, x.com, vxtwitter.com, fxtwitter.com)
2. Cliquer sur **Générer**
3. Attendre le traitement (extraction + création vidéo)
4. Télécharger la vidéo générée
