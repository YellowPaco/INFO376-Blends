# INFO 376 (Spotify) Blends Project

**NOTE FOR TEAM:** We have 4 users (andy, dishita, riya, priyanka) and thousands of unique tracks combined across our listening histories. That means our system is content-first (audio features + tags) with light collaborative filters (how the 4 of us overlap), not a heavy collaborative-filtering system that needs millions of users (hopefully it will be in the future).

At a high level, the system is a three-stage hybrid recommender that uses our enriched Spotify listening history (audio features, genre/mood tags, and behavioral signals like skips and time played) to generate personalized, mood-aware playlists. It combines content-based and lightweight collaborative signals.

# Overall System Overview

## Goals

* Generate personalized playlists that match each team member's long-term taste and current mood/context.
* Use the enriched Spotify data we already have
  * Audio features: danceability, energy, valence, tempo, loudness, acousticness, instrumentalness, speechiness, liveness, key, mode, time\_signature.
  * Categorical tags: sc\_genres, all\_tags (our main tag set), plus artist and album metadata.
  * Behavior: ms\_played, skipped, timestamps (ts), platform, etc.
* Implement a hybrid recommender:
  * Strong content-based core (driven by each person's own history and track features).
  * Light collaborative/social signal based on overlaps between the 4 users.

## Three-stages

* **Stage 1 - Candidate Generation**
  * Narrow the full track corpus down to 1,000 candidates per user using content k-NN, tag-based expansion, and neighbor expansion.
* **Stage 2 - Hybrid Scoring**
  * For each candidate, compute a richer score that combines:
    * Content similarity to the user's taste and current mood
    * Light collaborative/group signals (how the other 3 users listen to the track)
* **Stage 3 - Re-ranking & Playlist Construction**
  * MAP-optimized re-ranking using historical play data as ground truth, followed by an explicit thumbs up/down feedback pass from each user, producing a final 100-track playlist per person exported as a PDF.
* Each stage is modular and can be iterated on independently.

# Data Model and Core Representations

JSON → well-defined tables (CSV) → vectors

## Raw event format (per play)

Each play event (row) from the enriched JSON has fields like:

* Metadata
  * master\_metadata\_track\_name
  * master\_metadata\_album\_artist\_name
* Behavior
  * ts (timestamp)
  * platform
  * ms\_played
* Audio features
  * danceability, energy, valence, tempo, loudness, acousticness, instrumentalness, speechiness, liveness, key, mode, time\_signature
* Tags
  * sc\_genres
  * lastfm\_tags
  * mb\_tags
  * all\_tags — our "best available" tag field, resolved in priority order: all\_tags → sc\_genres → lastfm\_tags

## Item (track) vectors

For each unique track in the combined corpus of all 4 users, we build a canonical item vector:

* Continuous features (from audio\_features), normalized to [0, 1] with MinMaxScaler:
  * [danceability, energy, valence, tempo, loudness, acousticness, instrumentalness, speechiness, liveness, key, mode, time\_signature]
* Tag/categorical features:
  * all\_tags as a TF-IDF vector (min\_df=2, max 500 features)
  * Artist identity as an integer category code

We store a track table keyed by spotify\_track\_uri with:

* track\_id (integer index)
* Metadata (track\_name, artist\_name, album\_name)
* artist\_id

Fitted scalers saved to `models/` for reuse.

## User profiles (for 4 users)

For each of us, we build:

* **Per-(user, track) play stats**
  * n\_plays, avg\_ms, skip\_rate, meaningful\_pct
  * A play is `meaningful` if not skipped AND ms\_played >= 30,000ms
  * Play `weight` = meaningful\_pct × log1p(n\_plays) — log dampens obsessive replays so 10 plays don't outweigh 2 plays by 5x
  * Weight is zeroed out for tracks where skip\_rate > 0.7 and avg\_ms < 15,000ms (consistent early bails)
  * Stored as a sparse user × track weight matrix

* **Long-term taste profile**
  * Weighted average of normalized audio feature vectors across all of the user's tracks
  * Weights are the per-track play weights above
  * Result: a single vector representing overall taste across all moods

* **Mood clusters** (up to 4 per user via weighted KMeans)
  * Clustered on [valence, energy, danceability, tempo]
  * Each cluster stores a centroid and a weighted tag distribution
  * Session mood is detected by looking at the user's most recent 10 unique plays and picking whichever cluster they fall into most

* **Tag distribution per user**
  * Weighted average of TF-IDF tag vectors across the user's listening history
  * Captures which tags/genres are most characteristic of each user

* **User-user similarity matrix**
  * Pairwise cosine similarity between all 4 users' tag distributions
  * Used to weight neighbor contributions in collaborative signals

# (Spotify) Blends Recommender System Architecture

## Stage 1 - Candidate Generation

For each user, generates a pool of up to 1,000 tracks using three sources.

**Key parameters:** TOP\_N\_CONTENT=300, TOP\_N\_TAG=200, TOP\_N\_NEIGHBOR=150, FINAL\_CANDIDATE\_K=1000, TOP\_K\_TAGS=10

### Candidate Sources

* **Content k-NN**
  * Queries: the user's long-term taste profile + one modified query per mood centroid (mood feature values spliced into the long-term profile)
  * Similarity: cosine over the normalized audio feature matrix
  * Filters out tracks the user consistently skips early (skip\_rate > 0.7)

* **Tag-based expansion**
  * Scores every track by summing TF-IDF weights across the user's top K tags
  * Keeps only tracks that have been played by at least 1 person in the group
  * Returns the top TOP\_N\_TAG tracks by tag relevance score

* **Neighbor expansion**
  * Collects tracks that similar users listened to with avg\_ms >= 30,000ms and skip\_rate <= 0.30
  * Excludes tracks the target user has already played more than once
  * Neighbor contributions are weighted by user-user tag similarity
  * Returns the top TOP\_N\_NEIGHBOR tracks by weighted neighbor signal

### Pre-scoring and Merge

All three candidate sets are merged, and each track gets a pre-score:

```
pre_score = 0.50 * content_sim + 0.20 * tag_sim + 0.15 * group_pop + 0.15 * neighbor_score
```

Neighbor scores are normalized to [0, 1] before combination. Top 1,000 tracks by pre\_score move forward per user.

## Stage 2 - Hybrid Scoring

Given the 1,000-track candidate pool, computes a richer feature set and a final hybrid score for each (user, track).

### Features computed per candidate

* **Content features**
  * `long_term_sim`: cosine similarity to user's long-term audio profile
  * `tag_cosine`: cosine similarity to user's tag profile
  * `mood_sim`: cosine similarity to the active session mood centroid
  * `cluster_tag_sim`: cosine similarity to the active mood's tag distribution
  * `tag_jaccard`: Jaccard similarity of the track's top 5 tags vs. user's top 3 tags

* **Collaborative features**
  * `group_listener_count`: how many of the 4 users have played this track
  * `group_avg_ms`: average listen duration across the group
  * `group_skip_rate`: average skip rate across the group
  * `weighted_nbr_avg_ms`: neighbor-weighted average listen duration

* **Session features**
  * From the user's last 10 unique plays: recent skip rate, avg valence, avg energy
  * Per candidate: valence\_delta and energy\_delta vs. those recent averages

* **Context features**
  * Binary indicators for time-of-day bucket (morning, afternoon, evening, night) and platform (mobile, desktop)
  * Optional — defaults to None if not passed in

### Scoring

All components are min-max normalized to [0, 1] before combination.

```
s_content = 0.30 * long_term_sim + 0.25 * tag_cosine + 0.20 * mood_sim + 0.15 * cluster_tag_sim + 0.10 * tag_jaccard

s_collab  = 0.30 * group_listener_count + 0.25 * group_avg_ms + 0.20 * (1 - group_skip_rate) + 0.25 * weighted_nbr_avg_ms

hybrid_score = w_content * s_content + w_collab * s_collab
```

Default weights: w\_content = 0.80, w\_collab = 0.20. Optionally, weights can be learned from binary play history labels via logistic regression (use\_learned\_weights=True); falls back to defaults if fewer than 10 labeled examples exist in the pool.

Output: top 150 tracks per user sorted by hybrid\_score.

## Stage 3 - Re-ranking and Playlist Construction

Two sequential passes turn the 150-track scored pool into a final playlist.

### Pass 1: MAP Re-ranking

Uses the user's full play history as implicit ground truth to find the (w\_content, w\_collab) split that maximizes Average Precision within the scored pool.

A track is a ground-truth positive if avg\_ms > 45,000ms and skip\_rate < 0.20. If fewer than 3 positives exist in the pool at those thresholds, we walk a fallback ladder: (30k / 0.30) → (20k / 0.40) → (10k / 0.50). We only accept new weights if AP strictly improves over the original ordering; otherwise defaults are kept.

Re-scores and re-ranks the 150-track pool using the best weights found.

### Pass 2: Explicit Feedback Re-ranking

An ipywidgets UI lets each user rate their top 20 recommended songs with 👍/👎 buttons. Ratings are saved to `data/processed/feedback_{user}.json`.

Re-ranking logic:
  * Compute audio centroids for liked and disliked tracks
  * `feedback_score = map_score + 0.30 * like_sim − 0.20 * dislike_sim`
  * Remove explicitly disliked tracks entirely
  * Return top 100 tracks

### PDF Export

A styled dark-theme PDF is generated for each user (via ReportLab) showing the numbered track list with track name and artist. Saved to `results/playlists/playlist_{user}.pdf`.

# End-to-End Flow

Run `Blends.ipynb` top to bottom:

1. Imports and configuration (global parameters for the entire pipeline live here)
2. Load and clean all four users' JSON histories
3. Build track table: normalized audio matrix + TF-IDF tag matrix
4. Build user profiles: play stats, long-term profiles, tag profiles, mood clusters, user-user similarity
5. Candidate generation → 1,000 candidates per user
6. Hybrid scoring → top 150 tracks per user
7. MAP re-ranking
8. Feedback widget — each user rates their top 20 songs
9. Feedback re-ranking → final 100-track playlists
10. PDF export

**File layout:**
```
data/
  raw/           enrichment-success-{user}.json       (input)
  processed/     events.csv, track_features.csv,
                 play_stats.csv, user_profiles.csv,
                 user_tag_profiles.csv, mood_clusters_{user}.csv,
                 candidates_{user}.csv, hybrid_scored_{user}.csv,
                 map_reranked_{user}.csv, feedback_{user}.json,
                 final_playlist_{user}.csv
models/          audio_scaler.joblib, tfidf.joblib, mood_scaler.joblib
results/
  playlists/     final_playlist_{user}.csv, playlist_{user}.pdf
```

**Evaluation and iteration:**

* MAP re-ranking prints AP before/after per user so we can see whether the optimization helped.
* Qualitative feedback from each person via the widget feeds directly into the final re-ranking.
* We can redefine:
  * Scoring weights (W\_CONTENT, W\_COLLAB and sub-weights inside the content/collab scoring functions)
  * Candidate pool sizes (TOP\_N\_CONTENT, TOP\_N\_TAG, TOP\_N\_NEIGHBOR, FINAL\_CANDIDATE\_K)
  * Mood cluster count (N\_MOOD\_CLUSTERS)
  * What "meaningful" means (MIN\_MEANINGFUL\_MS)
  * MAP thresholds and fallback ladder
  * Feedback influence weights and final playlist length