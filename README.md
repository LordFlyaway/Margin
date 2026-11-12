# Margin

A Windows study app. Upload your lecture notes and Margin writes clean notes, flashcards, practice exams and a day-by-day revision plan from them. An always-on assistant answers your doubts from your own notes. Type a question, or say **"hey Jarvis"** and ask out loud.

It runs on your PC. You can use free local AI models through Ollama, or Claude or ChatGPT with your own API key.

## Features

- **Library:** upload PDFs, PowerPoint, Word, text and Markdown files, and photos of handwritten notes. Drop files on **All subjects** and the AI sorts them into subjects, or drop them on a subject to file them there. Long file names like `FALLSEM…_Module-4 (1)` are shortened to `Module 4`. PowerPoint slides (`.pptx` and old `.ppt`) are turned into PDFs, so they open in the reader looking exactly like the slides. This needs Microsoft PowerPoint installed; without it, `.pptx` files show their text instead.
- **Reader:** open any document inside Margin, with the assistant beside it.
- **Notes:** full notes, a summary or a cheat sheet for any document. Key terms are highlighted and every point links back to its source page.
- **Flashcards:** generated from your documents and scheduled with spaced repetition, so cards you get wrong come back sooner.
- **Practice exams:** multiple-choice and written questions, marked by the AI, with your weak topics tracked.
- **Papers:** add a subject's past papers and Margin writes new full papers in the same format (sections, marks, time, question style) from your study material, with a marking scheme you can show or hide, and printing.
- **Exam plan:** enter an exam date and get a daily schedule of learning, review and a mock exam. Weak topics go first. Ticked tasks move to **Done today**.
- **Assistant:** answers doubts from your notes, with page citations. Hands-free with "hey Jarvis", or press **Talk**. Attach PDFs, Word and PowerPoint files, or pictures, and ask about them.
- **Chat:** the same assistant as a full page, for longer conversations.
- **Music:** a Spotify mini player in the sidebar with cover art, progress, previous, play/pause, next and volume. You can also save study playlists.
- **Tray app:** closing the window keeps Margin listening in the system tray.

## Requirements

- Windows 10 or 11
- [Python 3.12](https://www.python.org/downloads/) (tick "Add python.exe to PATH" during install)
- [Ollama](https://ollama.com/download) for free local AI, or a Claude API key from [console.anthropic.com](https://console.anthropic.com), or an OpenAI API key from [platform.openai.com](https://platform.openai.com)
- A microphone, for the voice assistant
- The Spotify desktop app, for the music player (optional)
- An NVIDIA GPU with 8 GB or more is recommended for local models. They also run on the CPU, just slower.

## Install

1. Get the code:

   ```bash
   git clone https://github.com/LordFlyaway/Margin.git
   cd Margin
   ```

2. Create a virtual environment and install the packages. This downloads a few GB, mostly PyTorch:

   ```bash
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```

3. Download the local AI models. Skip this if you'll only use Claude:

   ```bash
   ollama pull qwen2.5:7b
   ollama pull glm-ocr:q8_0
   ollama pull nomic-embed-text
   ```

   | Model | Size | Job |
   |---|---|---|
   | `qwen2.5:7b` | 4.7 GB | Writes notes, flashcards and exams, and answers your questions |
   | `glm-ocr:q8_0` | 1.8 GB | Reads scanned pages, photos and image-only slides |
   | `nomic-embed-text` | 0.3 GB | Searches your notes by meaning |

4. Start Margin:

   ```bash
   run.bat
   ```

The first launch takes a minute longer while the voice models download (wake word, speech recognition and the speaking voice, about 150 MB). After that, Margin starts in a few seconds.

**Desktop shortcut (optional):** right-click `run.bat`, choose **Show more options → Send to → Desktop (create shortcut)**. To start Margin with Windows instead, turn on **Settings → Startup**.

## Using Margin

### 1. Add your notes

Open **Library** and drop your files on the page. Margin reads each one and sorts it into a subject. If one lands in the wrong place, change it with the **Subject** dropdown under the document, or rename it with **Rename**.

### 2. Make study material

Each document has these buttons:

- **Make notes:** pick Full notes, Summary or Cheat sheet first. Notes appear in **Notes**, grouped by subject.
- **Flashcards:** choose how many. Review them in **Flashcards**: click the card or press space to flip it, then grade yourself with Again, Hard, Good or Easy (keys 1 to 4).
- **Practice exam:** choose how many questions, answer them, then **Hand in** to have them marked.

### 3. Plan for an exam

In **Exam plan**, enter the exam name, subject, date and how many minutes a day you can study. Leave the topics empty and the AI works them out from your notes. Your daily tasks appear on **Today**. After a practice exam, **Rebuild around weak topics** reschedules with your weak areas first.

### 4. Practise with full papers

Open **Papers** and pick a subject.

1. Drop the subject's past papers under **Past papers**. Scans and photos work too.
2. Under **Make a new paper**, untick any modules the exam won't cover, then click **Make paper**.
3. Open it from **Your practice papers**. It looks like a real exam sheet. Tick **Show answers** for the marking scheme, or **Print** it to write on paper.

New papers follow the past papers' sections, number of questions, marks, time and wording, but the questions are new and come from your study material. Questions too similar to a past paper are thrown out and rewritten. With no past papers, Margin uses a standard format: Part A, 10 short questions of 2 marks; Part B, answer any 5 of 7 questions of 10 marks. Claude writes noticeably better papers than the local model, which occasionally gets subpart marks wrong.

### 5. Ask the assistant

The panel on the right is always available.

- Type a question and press Enter, or say **"hey Jarvis"** and then your question. It answers out loud, and you have 4 seconds to ask a follow-up without saying the wake word again.
- **Talk** starts listening without the wake word. **Mute** turns the microphone off.
- It can also do things for you. Type or say instructions like these:

  | Say | What happens |
  |---|---|
  | "Make notes for every Operating Systems module" | Writes full notes for each document in that subject, skipping any that already have them |
  | "Make a cheat sheet for module 3 of OS" | Writes a cheat sheet for that one document |
  | "Make 15 flashcards for Cloud Arch module 6" | Creates flashcards |
  | "Quiz me on Operating Systems module 2" | Writes a practice exam |
  | "Make a practice paper for Operating Systems" | Writes a full paper in the format of your past papers |
  | "My Cloud Arch exam is on 28 November, make me a plan" | Builds a revision plan |
  | "Open my flashcards" | Opens that page |
  | "Pause the music", "skip this song", "turn it down", "set the volume to 40" | Controls Spotify |
- **Attach files:** click the paperclip, drag files onto the panel, or paste a screenshot. Up to 6 PDFs, Word, PowerPoint, text files or pictures per message. Claude and ChatGPT look at pictures directly; the local model reads their text first.
- **Chat** in the sidebar opens the assistant as a full page. **New chat** clears the conversation and its attachments.
- **Search in** limits answers to one subject. **Answer with** switches between the local model, Claude and ChatGPT.
- Click a page citation like **p. 12** to see the source text, then **Open the document here** to jump to it.

### 6. Music

Play something in the Spotify desktop app and it appears at the bottom of the sidebar, with cover art, progress, controls and a volume slider. The slider sets Spotify's level in the Windows volume mixer. To save playlists, paste Spotify links in **Music**: **Open in Spotify** plays full tracks, and **Play here** keeps a small player beside your notes but may only play 30-second previews. No Spotify login or developer key is needed.

## Settings

| Setting | What it does |
|---|---|
| **Thinking** | Claude, ChatGPT or Local, for notes, flashcards, exams, papers and answers |
| **Reading scanned pages** | Claude or Local, for pages with no selectable text. Claude is best for handwriting. |
| **Graphics card** | **Balanced** (default) uses the GPU only for answers and reads documents on the CPU. **Light** also splits answers with the CPU, which is slower but cooler. **Full** puts everything on the GPU. |
| **Claude** | Paste your API key. It's stored in Windows Credential Manager, never in a file. Choose Opus 5.5, Sonnet 5.5 or Haiku 5.5. |
| **ChatGPT** | Paste your OpenAI API key, also stored in Windows Credential Manager. Once the key is saved, the model list shows every model your account can use. |
| **Voice** | Turn the wake word on or off, adjust its sensitivity, and choose whether answers are spoken |

### Using Claude

Claude gives noticeably better notes and reads handwriting well. You pay per use through an Anthropic API account; a Claude Pro or Max subscription doesn't cover it. As a rough guide, full notes for a 100-page PDF cost about $0.50 with Opus 5.5, $0.25 with Sonnet 5.5, or under $0.05 with Haiku 5.5. Answering a question costs much less.

### Using ChatGPT

Paste an OpenAI API key in **Settings → ChatGPT**, then pick **ChatGPT** under **Thinking** or in **Answer with**. API use is paid per use through platform.openai.com; a ChatGPT Plus subscription doesn't include it. The mini models cost much less and handle most doubts well.

## Privacy

- Your documents, notes and history are stored in the `data` folder on your PC.
- The microphone is analysed on your PC to hear the wake word. Audio is never saved or uploaded.
- With **Local** selected, nothing leaves your PC. With **Claude** or **ChatGPT** selected, your question, the matching excerpts from your notes and any files you attach go to Anthropic or OpenAI, along with a document's text when you generate notes, flashcards or exams from it.
- Margin's server only accepts connections from your own PC.

## Troubleshooting

| Problem | Fix |
|---|---|
| Nothing happens when I open Margin | It's probably already running in the system tray. Opening it again brings the window back. |
| "Ollama isn't running" | Open the Ollama app, or switch to Claude in Settings. |
| "The local model … isn't installed" | Run the `ollama pull` commands in Install step 3. |
| "hey Jarvis" doesn't respond | Check the microphone in Windows sound settings, then lower the sensitivity in Settings → Voice. Or use **Talk**. |
| "hey Jarvis" triggers by itself | Raise the sensitivity in Settings → Voice. |
| "OpenAI doesn't offer the model…" | Pick another model in Settings → ChatGPT. |
| The music player doesn't appear | Open the Spotify desktop app and play something. |
| A page doesn't update after an update | Press F5 in the Margin window. |

## For developers

The app is a FastAPI server (`app/server.py`) with a vanilla JavaScript interface (`app/static`), shown in a desktop window by pywebview (`desktop.py`). Data is stored in SQLite.

| File | Purpose |
|---|---|
| `app/ingest.py` | Reads files into pages, and pages into search chunks |
| `app/study.py` | Notes, flashcards, exams and plans |
| `app/papers.py` | Practice papers in the format of past papers |
| `app/assistant.py` | Answers questions from your notes |
| `app/llm.py` | One interface over Claude, ChatGPT and Ollama |
| `app/voice/engine.py` | Wake word, recording, speech-to-text and speech |
| `app/music.py` | Spotify links, now playing, controls and volume |

Run the tests:

```bash
.venv\Scripts\python -m pytest tests
```
