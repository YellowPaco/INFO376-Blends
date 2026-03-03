# INFO 376 (Spotify) Blends Project

**NOTE FOR TEAM:** For starters, we have 4 users (our team) and thousands of unique tracks combined across our listening histories. That means our system is content-first (audio features \+ tags) with light collaborative filters (how the 4 of us overlap), not a heavy collaborative-filtering system that needs millions of users (hopefully it will be in the future).

At a high level, the system is a three-stage hybrid recommender that uses our enriched Spotify listening history (audio features, genre/mood tags, and behavioral signals like skips and time played) to generate personalized, mood-aware playlists. It combines content-based and lightweight collaborative signals.

# Overall System Overview

## Goals

* Generate personalized playlists that match each team member’s long-term taste and current mood/context.  
* Use the enriched Spotify data we already have  
  * Audio features: danceability, energy, valence, tempo, loudness, acousticness, instrumentalness, speechiness, liveness, key, mode, time\_signature.  
  * Categorical tags: sc\_genres, all\_tags (our main tag set), plus artist and album metadata.  
  * Behavior: ms\_played, skipped, reason\_start, reason\_end, timestamps (ts), platform, etc.  
* Implement a hybrid recommender:  
  * Strong content-based core (driven by each person’s own history and track features).  
  * Light collaborative/social signal based on overlaps between the 4 users.

## Three-stages

* **Stage 1 \- Candidate Generation**  
  * Narrow the full track corpus down to a few thousand to hundreds of candidates per using, using content similarities and simple group statistics.  
* **Stage 2 \- Hybrid Scoring**  
  * For each candidate, compute a richer score that combines:  
    * Content similarity to the user’s taste and current mood  
    * Light collaborative/group signals (how the other 3 users listen to the track)  
* **Stage 3 \- Re-ranking & Playlist Construction**  
  * Apply mood/context logic, skip-based filters, and diversity rules to produce final playlists tailored to each user and each mood.  
* Each stage is modular and can be implemented and iterated on independently

# Data Model and Core Representations

JSON → well-defined tables (CSV) → vectors

## Raw event format (per play)

Each play event (row) from the enriched JSON has fields like:

* Metadata  
  * master\_metadata\_track\_name  
  * master\_metadata\_album\_artist name  
* Behavior  
  * ts (timestamp)  
  * platform  
  * ms\_played  
* Audio features  
  * audio\_features: danceability, energy, valence, tempo, loudness, acousticness, instrumentalness, speechiness, liveness, key, mode, time\_signature  
* Tags  
  * sc\_genres  
  * lastfm\_tags  
  * mb\_tags  
  * all\_tags (our “best available” tag track)

## Item (track) vectors

For each unique track in the combined corpus of all 4 users, we build a canonical item vector:

* Continuous features (from audio\_features):  
  * Ex vector \- \[danceability, energy, valence, tempo, loudness, acousticness, instrumentalness, speechiness, liveness, key, mode, time\_signature\]  
* Tag/categorical features:  
  * all\_tags as a TF-IDF vector over a tag vocabulary  
  * Optionally: sc\_genres mapped into a smaller set of genre groups  
  * Artist identity (ex: integer ID that can later be embedded for experiments).

We store a track table keyed by spotify\_track\_uri with

* track\_id  
* Metadata (name, artist, album)  
* Continuous feature vector  
* Tag vector

## User profiles (for 4 users)

For each of us, we build:

* **Long-term taste profile**  
  * Weighted average of item vectors for tracks they’ve listened to  
  * Weights  
    * Higher if ms\_played is high (they listened to most of the track)  
    * Lower or zero if skipped \== true and ms\_played is low  
  * Result: a single vector representing that user’s “overall taste” across all moods.  
* **Mood/tempo clusters**  
  * Take all tracks the user has listened to and consider:  
    * \[valence, energy, danceability, tempo\] (maybe acousticness).  
  * Cluster these into a small number of mood clusters per user (ex: 3-5 clusters):  
    * Cluster example:  
      * Low energy, low valence, acoustic → “chill/sad/acoustic”  
      * High energy, high danceability, mid valence → “party/workout”  
      * Mid energy, 90’s RnB tag→ “throwbacks mood”  
  * For each cluster, store:  
    * A centroid vector  
    * Aggregate tag distribution for that cluster  
* **Tag distribution per user**  
  * Aggregate all\_tags (and possibly sc\_genres) over their tracks  
  * Compute normalized counts or TF-IDF weights per tag  
  * This captures which tags are especially characteristic of that user (ex: “pop”, “hip-hop”, etc.)  
* **Simple per-track user stats**  
  * For each (user, track) pair:  
    * Number of plays  
    * Average ms\_played  
    * Personal skip rate  
* These representations are computed offline and cached. Should be manageable

# (Spotify) Blends Recommender System Architecture

## Stage 1 \- Candidate Generation

For each user, generate a candidate pool of maybe 500-1500 tracks that are likely to be relevant, using fast similarity logic. With thousands of tracks and only 4 users, this should be enough to ensure good coverage while staying efficient.

### Candidate Scores

We use three main sources (remember with just our team, content-based signals are primary as the “neighbor”/collaborative pieces are small add-ons).

* **Content k-NN (profile/mood → tracks)**  
  * For each user profiles  
    1. Use the long-term taste profile vector  
    2. Use each of their mood centroids  
  * For each of these vectors  
    1. Find the nearest tracks in the track space using cosine similarity on continuous features.  
    2. Tag similarity (ex: cosine / Jaccard on tag vectors)  
  * Return top N tracks from:  
    1. Long-term profile neighbors  
    2. Each mood centroid’s neighbors (mood-specific candidates)  
  * Filter  
    1. Drop tracks where this user has a very high personal skip rate  
    2. Optionally reduce score for tracks overplayed recently  
* **Tag-based expansion**  
  * Take the user’s tag distribution:  
    1. Extract their top K tags (genres)  
  * For each top tag:  
    1. Retrieve tracks with that tag in all\_tags and that are:  
       * Popular in the group (played by multiple users)  
       * Or at least reasonably listened to by anyone  
  * This curates songs that match the user’s tastes and are also “socially relevant” within our group.  
* **Simple group / neighbor expansion (light collaborative)**  
  * Define user-user similarity as  
    1. Cosine between user tag distributions  
    2. Optionally add cosine between long-term taste profiles  
  * For a target user  
    1. Rank the other 3 users as neighbors  
    2. Collect tracks that  
       * Neighbors listen to it with high ms\_played and low skip rate.  
       * The target user has never or rarely played.  
  * This adds the aspect of “friends listen to this and it matches your tags”

### Merge and Pre-score

* **Merge the candidates from**  
  * Content k-NN  
  * Tag-based expansion  
  * Neighbor expansion  
* For each candidate track/song, **compute a simple pre-score**  
  * Ex: pre\_score \= max(content\_similarity\_from\_best\_centroid, group\_popularity\_score, neighbor\_signal  
* Sort candidates by pre\_score and keep the top N per user  
* This will give a list of candidate track IDs with basic pre-scores and metadata

## Stage 2 \- Hybrid Scoring

### Hybrid Scoring Logic

We are conceptually computing:

1. **Content score from content and tag feature** \- “does this track look like something this user likes and fits the inferred mood?”  
2. **Group/collaborative score from group features** \- “do other members of the group listen to this and like it?”  
3. **Score**   
   * w\_c \= content weight  
   * s\_c \= content-based score  
   * w\_cf \= collaborative/group weight  
   * s\_cf \= collaborative/group-based score  
   * score \= w\_c \* s\_c \+ w\_cf \* s\_cf  
4. **Output**  
   * For each user  
     * Compute the hybrid score for each candidate  
     * Sort candidates by score  
     * Keep the top K (100-200) for re-reranking

Example:  content weight \= 0.8 and collaborative weight \= 0.2  
Final score \= (80% "does this match my personal taste?") \+ (20% "do my teammates like this?")

**NOTE**: we can either hardcode the weights or have a small model learn the weights from labels built from ms\_played and skips.

Given the candidate list for a user, compute a richer relevance score per user (user, track) by combining detailed content similarity and group popularity/neighbor behavior.

### Technical

We construct a feature vector with:

* **Content-based features**  
  * Similarities:  
    * Cosine similarity between track vector and user’s long-term taste profile  
      * Cosine similarity between track vector and the mood centroid associated with the current session  
    * Tag overlap:  
      * Cosine similarly between track’s tag vector and user’s tag distribution  
      * Jaccard similarity (similarity between two sets) of top tags  
      * Binary indicators for presence of user’s top tags in the track.  
  * **Collaborative features**   
    * Group popularity:  
      * Number of users who listened to this track  
      * Total average ms\_played across the group.  
      * Group skip rate for this track  
    * Neighbor-based:  
      * For the target user, we can weigh other users by similarity and compute:  
        * Weighted number of neighbors who listened  
        * Weighted average ms\_played among neighbors  
  * **Short term/session features:**  
    * From the last N plays of this user  
      * Percentage of similar tracks that were skipped vs completed  
      * Average valence/energy of recent tracks (used to align with or contrast the candidate)  
    * Simple context (optional)  
      * Time-of-day bucket  
      * Platform (mobile vs. desktop)

## Stage 3 \- Re-ranking and Playlist Construction

### Objective

Transform the top K scored tracks into final playlist that:

* Match the user’s current mood and context  
* Avoid tracks the user strongly dislikes or is tired of  
* Maintain diversity and avoid being monotone or repetitive

### Session mood detection

For each user session:

* Look at the last N plays (10-30 events)  
  * Compute averages of valence, energy, danceability, tempo.  
  * Look at tag distributions of those tracks  
* Map this into one of a small set of mood labels, for example:  
  * “Chill / low energy acoustic”  
  * “Upbeat / dance / electronic”  
  * “Hip-hop / rap”  
  * “Indie / alternative”

### Hard Filters

On the top K scored tracks, apply:

* Personal dislike/fatigue  
  * Downrank or remove tracks the user frequently skips or barely listens to.  
  * Downrank tracks played many times in the recent window  
* Quality filters  
  * Remove tracks with missing essential metadata or audio features

### Mood-aware Re-ranking and Diversity

* Mood match:  
  * Boost tracks whose audio features fall into the ranges implied by the current mood label  
  * Boost tracks whose tags are consistent with the mood  
* Diversity  
  * Penalize consecutive tracks from the same artist  
  * Penalize tracks with very similar tag sets place back-to-back  
  * Optionally ensure a minimal mix of sub-genres within a playlist

### Final Playlist (per user)

From the re-ranked list, build a playlist. Each playlist is:

* A filtered and re-ordered slice of the re-ranked list  
* Defined by  
  * Target mood label  
  * Cutoff length  
  * Diversity 

# End-to-End Flow

* Offline:  
  * Person 1 builds features and profiles from the enriched data for all 4 users.  
  * Person 2 uses those to implement candidate generation and the hybrid scorer  
* At recommendation time for user U  
  * Fetch U’s profile and recent history  
  * Generate candidates via content k-NN, tags, group expansion  
  * Compute features and hybrid scores → output top K tracks  
  * Detect mood, re-rank with rules, and construct one or more playlists.  
  * Display playlists to users.  
* Evaluation and Iteration  
  * We will heavily rely on qualitative feedback from each person and simple metrics like “would i actually listen to this playlist”  
  * We can redefine:  
    * What “mood” means to us  
    * Features weights and scoring formulas  
    * Re-ranking rules and UI