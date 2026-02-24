#TODO: add to the read me adn the top of the file for rules and how to use it (+ what code does)
#TODO: implement better comments so code is clear

import json
import os
import re
import time
import argparse
import glob
from pathlib import Path
from tqdm import tqdm
import requests
import base64
from dotenv import load_dotenv

load_dotenv()

#config - pull from .env

SPOTIFY_CLIENT_ID     = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
LASTFM_API_KEY        = os.getenv("LASTFM_API_KEY", "")

OUTPUT_FILE = "streaming_history_enriched.json"
MB_USER_AGENT = "SpotifyHistoryEnricher/1.0 (email@email.com)"  # TODO: PUT YOUR EMAIL HERE


# cleaning lastfm tags cause they're informal and created by users
LASTFM_JUNK = re.compile(
    r"^(\d+s?$|seen live|favourite|favorites|loved|amazing|awesome|beautiful|"
    r"best|cool|epic|great|heard on|like$|love$|my |owned|perfect|under \d+|"
    r"spotify|youtube|pandora|radio|recommendations?|#\w+)",
    re.IGNORECASE,
)

def is_good_tag(tag: str) -> bool:
    tag = tag.strip().lower()
    if not tag or len(tag) > 40:
        return False
    if LASTFM_JUNK.match(tag):
        return False
    if tag.isdigit() or len(tag) == 1:
        return False
    return True


#spotify helpers

def get_spotify_token():
    creds = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
    resp = requests.post(
        "https://accounts.spotify.com/api/token",
        headers={"Authorization": f"Basic {creds}"},
        data={"grant_type": "client_credentials"},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def spotify_get(token, url, params=None, retries=3):
    headers = {"Authorization": f"Bearer {token}"}
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 3))
                time.sleep(wait)
                continue
            return resp
        except requests.exceptions.RequestException:
            if attempt == retries - 1:
                return None
            time.sleep(1)
    return None


def fetch_track_artist_id(token, track_id):
    """
    Fetch a single track to get its primary artist ID.
    Uses /tracks/{id} (single) because the batch /tracks endpoint
    returns 403 for newer Spotify apps.
    """
    resp = spotify_get(token, f"https://api.spotify.com/v1/tracks/{track_id}")
    if not (resp and resp.ok):
        return None
    artists = resp.json().get("artists", [])
    return artists[0]["id"] if artists else None


def fetch_artist_genres(token, artist_id):
    """Fetch genres for a single artist."""
    resp = spotify_get(token, f"https://api.spotify.com/v1/artists/{artist_id}")
    if not (resp and resp.ok):
        return []
    return resp.json().get("genres", [])


def fetch_audio_features_batch(token, track_ids):
    """Returns dict {track_id: features} or None if endpoint returns 403."""
    resp = spotify_get(
        token,
        "https://api.spotify.com/v1/audio-features",
        params={"ids": ",".join(track_ids)},
    )
    if resp is None or resp.status_code == 403:
        return None
    if not resp.ok:
        return {}
    return {f["id"]: f for f in resp.json().get("audio_features", []) if f}


#lastfm helpers

def fetch_lastfm_tags(artist, track, limit=8):
    """Fetch and clean tags. Falls back to artist tags if track has none."""
    if not LASTFM_API_KEY:
        return []

    def _get(method, **kwargs):
        try:
            r = requests.get(
                "https://ws.audioscrobbler.com/2.0/",
                params={"api_key": LASTFM_API_KEY, "format": "json",
                        "method": method, **kwargs},
                timeout=6,
            )
            return r.json()
        except Exception:
            return {}

    data = _get("track.getTopTags", artist=artist, track=track, autocorrect=1)
    raw = data.get("toptags", {}).get("tag", [])

    if not raw:
        data = _get("artist.getTopTags", artist=artist, autocorrect=1)
        raw = data.get("toptags", {}).get("tag", [])

    return [t["name"].lower() for t in raw if is_good_tag(t["name"])][:limit]


#musicbrainz helpers

def fetch_musicbrainz_tags(artist, track):
    """Search MusicBrainz for a recording and return its tags. 1 req/sec limit."""
    try:
        resp = requests.get(
            "https://musicbrainz.org/ws/2/recording/",
            params={
                "query": f"recording:{track} AND artist:{artist}",
                "fmt": "json",
                "limit": 1,
            },
            headers={"User-Agent": MB_USER_AGENT},
            timeout=8,
        )
        if not resp.ok:
            return []
        recordings = resp.json().get("recordings", [])
        if not recordings:
            return []
        rec = recordings[0]
        combined = {t["name"].lower() for t in rec.get("tags", []) + rec.get("genres", [])}
        return list(combined)[:5]
    except Exception:
        return []


#utils 

def extract_track_id(uri):
    if uri and uri.startswith("spotify:track:"):
        return uri.split(":")[-1]
    return None


def load_history_files(patterns):
    all_entries = []
    for pattern in patterns:
        for filepath in sorted(glob.glob(pattern)):
            print(f"  Loading {filepath}...")
            with open(filepath, "r", encoding="utf-8") as f:
                all_entries.extend(json.load(f))
    return all_entries


def chunked(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", nargs="+", default=["data/Streaming_History_Audio_*.json"])
    parser.add_argument("--output", default=OUTPUT_FILE)
    parser.add_argument("--skip-audio-features", action="store_true")
    parser.add_argument("--skip-spotify-genres", action="store_true",
                        help="Skip Spotify genre lookup (saves time if your app gets 403 on /tracks)")
    parser.add_argument("--skip-musicbrainz", action="store_true")
    args = parser.parse_args()

    #load data

    print("Loading streaming history...")
    entries = load_history_files(args.input)
    if not entries:
        print("No entries found. Check --input path.")
        return
    print(f"Total entries: {len(entries)}")

    track_entries = [e for e in entries if extract_track_id(e.get("spotify_track_uri"))]
    unique_track_ids = list({extract_track_id(e["spotify_track_uri"]) for e in track_entries})
    print(f"Track entries: {len(track_entries)} | Unique tracks: {len(unique_track_ids)}")

    #auth (spotify)
    print("\nAuthenticating with Spotify...")
    token = get_spotify_token()
    token_time = time.time()

    def maybe_refresh():
        nonlocal token, token_time
        if time.time() - token_time > 3000:
            token = get_spotify_token()
            token_time = time.time()

    #doesn't work, skip on run (spotify closed their audio features part in 2024)

    audio_features_map = {}

    if not args.skip_audio_features:
        print("\nFetching audio features (batches of 100)...")
        for batch in tqdm(list(chunked(unique_track_ids, 100))):
            maybe_refresh()
            result = fetch_audio_features_batch(token, batch)
            if result is None:
                print("\n  ⚠️  Audio features returned 403 — deprecated for new Spotify apps.")
                print("     Re-run with --skip-audio-features to suppress this.")
                break
            audio_features_map.update(result)
            time.sleep(0.1)
        print(f"  Got audio features for {len(audio_features_map)} tracks.")

    #spotify genres

    # Use single-track fetches (/tracks/{id}) because the batch
    # endpoint (/tracks?ids=...) returns 403 for newer Spotify developer apps as of 2024 i guess.
    track_spotify_genres = {}

    if not args.skip_spotify_genres:
        print(f"\nFetching artist IDs via single-track calls ({len(unique_track_ids)} tracks)...")
        print("  (Using single fetches — Spotify batch endpoint is 403 for new apps)")

        track_to_artist_id = {}
        for track_id in tqdm(unique_track_ids):
            maybe_refresh()
            artist_id = fetch_track_artist_id(token, track_id) #TODO: DEBUG - figure out why this isn't getting a response
            if artist_id:
                track_to_artist_id[track_id] = artist_id
            time.sleep(0.05)  # ~20 req/sec, API rate limits

        print(f"  Mapped {len(track_to_artist_id)} / {len(unique_track_ids)} tracks")

        # make it so its only unique artists (spotify does genres by artist)
        unique_artist_ids = list(set(track_to_artist_id.values()))
        print(f"Fetching genres for {len(unique_artist_ids)} unique artists...")
        artist_genres_map = {}

        for artist_id in tqdm(unique_artist_ids):
            maybe_refresh()
            genres = fetch_artist_genres(token, artist_id)
            artist_genres_map[artist_id] = genres
            time.sleep(0.05)

        track_spotify_genres = {
            tid: artist_genres_map.get(aid, [])
            for tid, aid in track_to_artist_id.items()
        }

        genre_count = sum(1 for g in track_spotify_genres.values() if g)
        print(f"  Tracks with Spotify genres: {genre_count} / {len(unique_track_ids)}")
        if genre_count == 0:
            print("  ℹ️  All artists returned empty genres — Spotify hasn't classified them.")
            print("     This is common for niche artists. Last.fm will cover these.")

    #lastfm

    lastfm_cache = {}

    if LASTFM_API_KEY:
        seen = set()
        unique_for_lastfm = []
        for e in track_entries:
            key = (
                e.get("master_metadata_album_artist_name", ""),
                e.get("master_metadata_track_name", ""),
            )
            if key not in seen and any(key):
                seen.add(key)
                unique_for_lastfm.append(key)

        print(f"\nFetching Last.fm tags for {len(unique_for_lastfm)} unique tracks...")
        for artist, track in tqdm(unique_for_lastfm):
            lastfm_cache[(artist, track)] = fetch_lastfm_tags(artist, track)
            time.sleep(0.05)

        hit = sum(1 for v in lastfm_cache.values() if v)
        print(f"  Got tags for {hit} / {len(unique_for_lastfm)} tracks.")
    else:
        print("\nSkipping Last.fm (no LASTFM_API_KEY in .env).")

    #musicbrainz

    mb_cache = {}

    if not args.skip_musicbrainz:
        needs_mb = []
        seen_mb = set()
        for e in track_entries:
            tid    = extract_track_id(e.get("spotify_track_uri"))
            artist = e.get("master_metadata_album_artist_name", "")
            track  = e.get("master_metadata_track_name", "")
            key    = (artist, track)
            if (not track_spotify_genres.get(tid)
                    and not lastfm_cache.get(key)
                    and key not in seen_mb
                    and any(key)):
                seen_mb.add(key)
                needs_mb.append(key)

        if needs_mb:
            print(f"\nFetching MusicBrainz tags for {len(needs_mb)} remaining tracks...")
            print("  (Strictly rate-limited to 1 req/sec)")
            for artist, track in tqdm(needs_mb):
                mb_cache[(artist, track)] = fetch_musicbrainz_tags(artist, track)
                time.sleep(1.05)
            hit = sum(1 for v in mb_cache.values() if v)
            print(f"  Got tags for {hit} / {len(needs_mb)} tracks.")
        else:
            print("\nAll tracks covered — skipping MusicBrainz.")
    else:
        print("\nSkipping MusicBrainz (--skip-musicbrainz flag).")

    #add the tags

    print("\nApplying enrichment to all entries...")
    FEATURE_KEYS = ["danceability", "energy", "key", "loudness", "mode",
                    "speechiness", "acousticness", "instrumentalness",
                    "liveness", "valence", "tempo", "time_signature"]

    for entry in entries:
        tid    = extract_track_id(entry.get("spotify_track_uri"))
        artist = entry.get("master_metadata_album_artist_name", "")
        track  = entry.get("master_metadata_track_name", "")
        key    = (artist, track)

        features = audio_features_map.get(tid) if tid else None
        entry["audio_features"] = (
            {k: features[k] for k in FEATURE_KEYS if k in features} if features else None
        )
        entry["genres"]      = track_spotify_genres.get(tid, []) if tid else []
        entry["lastfm_tags"] = lastfm_cache.get(key, [])
        entry["mb_tags"]     = mb_cache.get(key, [])
        entry["all_tags"]    = entry["genres"] or entry["lastfm_tags"] or entry["mb_tags"]


    #summary

    with_features = sum(1 for e in entries if e.get("audio_features"))
    with_genres   = sum(1 for e in entries if e.get("genres"))
    with_lastfm   = sum(1 for e in entries if e.get("lastfm_tags"))
    with_mb       = sum(1 for e in entries if e.get("mb_tags"))
    with_any      = sum(1 for e in entries if e.get("all_tags"))

    print(f"\n{'='*52}")
    print(f"  Total entries:              {len(entries)}")
    print(f"  With audio features:        {with_features}")
    print(f"  With Spotify genres:        {with_genres}")
    print(f"  With Last.fm tags:          {with_lastfm}")
    print(f"  With MusicBrainz tags:      {with_mb}")
    print(f"  With ANY tag (all_tags):    {with_any}")
    print(f"{'='*52}")

    out_path = Path(args.output)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to: {out_path.resolve()}")
    print("\nNew fields on each entry:")
    print("  audio_features — danceability, energy, valence, etc. (None if unavailable)")
    print("  genres         — Spotify artist genres")
    print("  lastfm_tags    — Last.fm tags (cleaned)")
    print("  mb_tags        — MusicBrainz tags")
    print("  all_tags       — best available: genres > lastfm_tags > mb_tags")


if __name__ == "__main__":
    main()