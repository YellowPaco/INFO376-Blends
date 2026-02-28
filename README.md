# READ BEFORE USE

## Requirements to use the code:

Make the following free accounts:

- [SoundCharts](https://app.soundcharts.com) account - You should get 1000 free credits when you make your account (SOUNDCHARTS_APP_ID and SOUNDCHARTS_API_KEY needed)
- [LastFM](https://www.last.fm/api/account/create) account (API key needed)

Extended listening history data from Spotify (MUST be extended listening, have not yet adapted code to the account data format yet)

No longer necessary - related code doesn't have correct functionality

- Spotify Premium account to access [Spotify Web API](https://developer.spotify.com/documentation/web-api) (Client ID and Client Secret needed)
- [MusicBrainz](https://musicbrainz.org/register?returnto=%2F) account (email needed - remove from code when pushing to GitHub for privacy)
- JSON file of user data from Spotify (in their specific JSON formatting)

## Overview

### What does the code do?

Takes in Spotify Extended Listening Data and enriches each track entry with music metadata that Spotify doesn't include in their export — specifically audio features (danceability, energy, valence, tempo, etc.) and genre tags. Outputs a new JSON file in the same format as the original Spotify data, with the new fields added to every entry.

### How does the code do this?

Looks up each unique track across three external sources in priority order:

1. **SoundCharts** (primary) - one API call per track returns both audio features AND genres together. Results are saved to a local `sc_cache.json` file so you never spend credits on a track twice
2. **Last.fm** (fallback) - fills in genre tags for any tracks SoundCharts didn't cover. Tags are crowd-sourced and automatically cleaned to remove junk entries like "seen live" or "favorite"
3. **MusicBrainz** (last resort - also isn't currently returning anything) - catches anything still missing after the above two. Free with no account needed, but slow (rate-limited to 1 request/second)

---

## Setup

### Step 1 - Get your Spotify Extended Listening History

1. Log into Spotify and go to [Account Privacy Settings](https://www.spotify.com/account/privacy/)
2. Scroll down to **Download your data**
3. Check **Extended streaming history** (do NOT select "Account data", that comes in a different format that is not supported yet)
4. Click request and wait for Spotify to email you a download link (can take a few days up to a month)
5. Download and unzip the file. You should have one or more files named `Streaming_History_Audio_0.json`, `Streaming_History_Audio_1.json`, etc.
6. Put all of these files into a `data/` folder inside your project directory

### Step 2 - Get your SoundCharts credentials

1. Go to [app.soundcharts.com](https://app.soundcharts.com) and create a free account (no payment info needed!!).
2. You will automatically receive 1000 free API credits
3. Once logged in, go to **Settings -> API Keys**
4. You'll see a table with two things:
   - **App ID** — visible in plain text (looks like `CC-API_XXXXXXXX`) — this is your `SOUNDCHARTS_APP_ID` (put in .env)
   - **Token** — hidden by default — this is your `SOUNDCHARTS_API_KEY` (put in .env)
5. Click the eye icon next to the Token to reveal it, or use the copy icon to copy it without revealing

> **Important:** You only get 1000 credits total on a free account. Each unique track in your listening history costs 1 credit to look up. The script saves results to `sc_cache.json` after every single track, so if the script stops partway through you won't lose any data. You can just re-run it and it will skip everything already cached. If you run out of credits you can just create a new SoundCharts account and update your `.env` with the new credentials. The cache file makes it so that you only need to fetch the remaining tracks (saves API credits).

### Step 3 - Get your Last.fm API key

1. Go to [last.fm/api/account/create](https://www.last.fm/api/account/create). Log in or create a free account first if you don't have one
2. Fill out the API account form, you can put anything for the Name/Description
3. Submit the form and your **API Key** will be available on the next page
4. Copy the API Key (you do NOT need the Shared Secret for this project) -> put key in .env as `LASTFM_API_KEY`

### Step 4 - Set up your .env file

Create a file called `.env` in your project folder (you can copy from `.env.example`) and fill in your keys:

```
SOUNDCHARTS_APP_ID=CC-API_XXXXXXXX
SOUNDCHARTS_API_KEY=your_token_here
LASTFM_API_KEY=your_lastfm_key_here
```

> **Note:** Don't forget to add your `.env` to your `.gitignore`

### Step 5 - Install dependencies

```bash
pip install -r requirements.txt
```

### Step 6 - Run the script

The below command will skip all the code that doesn't currently work due to either depricated endpoints (Spotify) or endpoints that are currently either not returning anything or are not being processed correctly (MusicBrainz).

```bash
python enrich_spotify_data.py \
  --input data/Streaming_History_Audio_*.json \
  --skip-audio-features --skip-spotify-genres --skip-musicbrainz
```

The `*.json` glob automatically picks up all your Spotify history files and merges them into one enriched output. If you only have one file, you can also just pass it directly:

```bash
python enrich_spotify_data.py \
  --input data/Streaming_History_Audio_0.json \
  --skip-audio-features --skip-spotify-genres --skip-musicbrainz
```

---

## Output

The script saves to `streaming_history_enriched.json` by default. Every entry from your original Spotify data is kept exactly as-is, with these new fields added:

| Field            | Description                                                                           |
| ---------------- | ------------------------------------------------------------------------------------- |
| `audio_features` | danceability, energy, valence, tempo, loudness, key, mode, etc. `null` if unavailable |
| `sc_genres`      | Genre list from SoundCharts (e.g. `["r&b", "r&b, funk & soul"]`)                      |
| `lastfm_tags`    | Cleaned genre tags from Last.fm (e.g. `["neo soul", "jazz", "r&b"]`)                  |
| `mb_tags`        | Tags from MusicBrainz — only populated if the above two are both empty                |
| `all_tags`       | Best available tags from any source — use this one for modeling                       |

Example of what an enriched entry looks like:

```json
{
  "ts": "2026-01-15T01:44:56Z",
  "ms_played": 159242,
  "master_metadata_track_name": "What You Wanna Try",
  "master_metadata_album_artist_name": "Masego",
  "spotify_track_uri": "spotify:track:526fD9LiAEi3KKvhhYfWmm",
  "audio_features": {
    "danceability": 0.88,
    "energy": 0.58,
    "valence": 0.5,
    "tempo": 99.03,
    "acousticness": 0.07,
    "loudness": -5.93,
    "speechiness": 0.05,
    "instrumentalness": 0,
    "liveness": 0.09,
    "key": 2,
    "mode": 1,
    "time_signature": 4
  },
  "sc_genres": ["r&b", "r&b, funk & soul"],
  "lastfm_tags": ["neo soul", "r&b", "jazz"],
  "mb_tags": [],
  "all_tags": ["r&b", "r&b, funk & soul"]
}
```

---

## Optional flags

| Flag                     | What it does                                                             |
| ------------------------ | ------------------------------------------------------------------------ |
| `--skip-soundcharts`     | Skip SoundCharts entirely (not recommended - it's the best source)       |
| `--skip-musicbrainz`     | Skip MusicBrainz to save time if Last.fm coverage is good enough         |
| `--output filename.json` | Change the output file name (default: `streaming_history_enriched.json`) |

Example - skip MusicBrainz for a faster run:

```bash
python enrich_spotify_data.py --input data/Streaming_History_Audio_*.json --skip-musicbrainz
```

---

## Troubleshooting

**Getting 403 errors from SoundCharts**
Your credentials are wrong or your 1000 free credits are used up. Check your App ID and Token in `.env`. If out of credits, make a new account and update `.env`, the cache means you only pay for tracks not yet fetched.

**SoundCharts runs but returns 0 audio features / genres**
This was a bug in earlier versions of the script where the API response wasn't being parsed correctly. Make sure you have the latest version of `enrich_spotify_data.py`.

**Script hangs and stops moving**
Hit Ctrl+C to cancel, the cache saves after every track so no progress is lost. Re-run and it will skip everything already cached. This used to be caused by the Spotify genre lookup which would occasionally hang indefinitely due to deprication (ensure you are skipping Spotify genre/audio features!).
