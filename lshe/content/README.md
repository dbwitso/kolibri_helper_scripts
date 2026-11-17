# Making LSHE video channels

These steps turn a folder of LSHE videos into Kolibri channels, one per
grade band, that a centre server can import offline. They run on a
workstation with Docker and internet access. The centre server stays offline
and never needs either.

Kolibri can't import loose video files. It imports channels: a channel
database plus its files. The steps are:

1. **Convert** the videos to H.264 480p (`convert_videos.sh`).
2. **List** them in a manifest, and fill in band, licence and copyright
   holder (`make_manifest.py`).
3. **Build** the channels, one per band. Either:
   - **offline**, on this workstation (`build_offline_channels.sh`, section
     3). This is how the LSHE channels are made; or
   - on [Kolibri Studio](https://studio.learningequality.org)
     (`lshe_video_chef.py`, a ricecooker script, section 5), then publish
     them there and export them to disk.
4. **Copy** the export to the centre server and import it there
   (`../import_channels.sh`).

| File | Purpose |
| --- | --- |
| `Dockerfile` | The `lshe-content-tools` image: ffmpeg and ricecooker 0.8.0 |
| `convert_videos.sh` | Converts videos to H.264 480p with AAC audio, keeping the originals |
| `topics.csv.example` | Example topic descriptions and thumbnails |
| `make_manifest.py` | Lists the videos in a CSV manifest, with code and title from the file names |
| `build_offline_channels.sh` | Builds the bands' channels offline into a channel export, with `channels.conf` |
| `prepare_media.py` | Step one of the offline build: thumbnails and video lengths (tools image) |
| `build_channel.py` | Step two: the channel database, with Kolibri's own schema (Kolibri image) |
| `channel_common.py` | Reading and checking the manifest and topics, thumbnails; shared by all |
| `lshe_video_chef.py` | Builds one band's channel for Kolibri Studio instead; `dryrun`, or upload |

## 0. Build the tools image (once)

```sh
cd lshe/content
docker build -t lshe-content-tools .
```

ffmpeg and ricecooker run in the image, so nothing needs installing on the
workstation.

## 1. Convert the videos

Choose a work folder with room for the videos twice over, and unzip the
videos into `original/` inside it. Run every command below from this
folder (`lshe/content`):

```sh
W=/path/to/work/folder      # your work folder
./convert_videos.sh "$W/original" "$W/h264_480p"
```

Many low-cost tablets can't decode AV1 or other newer video formats, or can
only decode them slowly in software, and video in full HD is too heavy for a
whole class on one access point. The conversion makes every video H.264
(Main profile) at up to 480 lines, with AAC stereo audio. Every browser plays
this, and Kolibri treats it as its standard "low resolution". The originals
aren't changed. Re-running skips the videos already done.

## 2. List the videos

```sh
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/code:ro -v "$W":/w lshe-content-tools \
  python /code/make_manifest.py /w/h264_480p /w/manifest.csv
```

Open `manifest.csv` in a spreadsheet and fill in, for every video:

| Column | What to put |
| --- | --- |
| `band` | `G4-5` or `G6-7`, from the list UNESCO and the Ministry of Education approved. Only rows with the band being built are uploaded, so `HOLD` or an empty band keeps a video out |
| `topic` | Optional folder in the channel, for example `Relationships` |
| `license` | The licence the video is published under, for example `CC BY-NC-ND` or `CC BY-SA 4.0`. Check the producer's terms |
| `copyright_holder` | Who owns the video, for example the producer |
| `title`, `description` | Correct them if needed. They are what learners see |

Which videos suit which grades is not a technical decision. Use the approved
list. Re-running `make_manifest.py` adds new videos and keeps what you
filled in.

**Topic descriptions (optional, recommended).** Studio and Kolibri show a
description under each topic folder. Put them in `$W/topics.csv`, one row
per topic: columns `topic`, `band` (empty means every band, or a band name
for a description used in that band's channel only), `description`, and
`thumbnail` (optional: an image path relative to `topics.csv`). See
`topics.csv.example`. Each topic folder, and the channel itself, gets a
thumbnail tiled from frames of up to four of its videos, unless
`topics.csv` names one. To use your own image for the channel, add
`channel_thumbnail=/w/<image>.png` to the chef command (16:9 works best).

## 3. Build the channels offline

```sh
./build_offline_channels.sh "$W" G4-5 G6-7
```

This needs the `lshe-content-tools` image (step 0) and the LSHE Kolibri
image (`lshe-kolibri:latest`, built by `docker compose build` in the Kolibri
repository's `docker/lshe`). No internet and no Studio account are needed.
For each band it:

1. makes a thumbnail for every video (a frame from a quarter of the way in)
   and topic folder (from `topics.csv`, or frames of its videos tiled), and
   measures each video (`prepare_media.py`, in the tools image);
2. writes the channel database with Kolibri's own content schema and copies
   the videos and thumbnails in by checksum (`build_channel.py`, in the
   Kolibri image).

It writes in the work folder:

| Path | What it is |
| --- | --- |
| `offline/export/content/databases/<channel id>.sqlite3` | Each band's channel |
| `offline/export/content/storage/` | Videos and thumbnails, named by checksum |
| `offline/export/channels.conf` | The channel ids, for `import_channels.sh --file` |
| `offline/media/<band>/` | Thumbnails and `media.json`; kept so re-builds are quick |

This is the same layout as a Kolibri channel export (`exportchannel`,
`exportcontent`), so the server imports it in the usual way.

**Ids and versions.** The channel id comes from the band, and each video's
ids from its code, so they are the same in every build. Each build raises
the channel's version by one. To fix a title, change a topic or add videos,
edit the CSVs and run the same command again. A centre that imports the new
version keeps every learner's progress. To re-make a thumbnail, delete it
from `offline/media/<band>/` first.

The offline channels have their own ids, not the ids of the Studio channels
of the same name. Use one or the other on a server, never both.

**Checking a build.** To see the channels before a centre does, import the
export into any Kolibri 0.16 (**Device → Channels → Import → Local drive**,
or `kolibri manage importchannel disk <id> <export folder>` then
`importcontent disk`).

## 4. Import on the centre server

Copy the **contents** of `offline/export` (or of a Studio export, below) to
`/srv/lshe-content` on the centre server, for example from a USB drive, so
that `/srv/lshe-content/content/` exists. Then, as in `../README.md`:

```sh
sudo ./import_channels.sh --file /srv/lshe-content/channels.conf
sudo ./run_script.sh set_band_channels.py --band G4-5 <G4-5 channel id>
sudo ./run_script.sh set_band_channels.py --band G6-7 <G6-7 channel id>
```

The ids are in `channels.conf`.

## 5. Or: check and upload each band to Studio

ricecooker keeps its cache (`storage/`, `chefdata/`, `restore/`) in
`$W/chef`, outside the repository:

```sh
mkdir -p "$W/chef"
CHEF() {
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/run \
    -v "$PWD":/code:ro -v "$W":/w -v "$W/chef":/run -w /run \
    lshe-content-tools python /code/lshe_video_chef.py "$@"
}

# Dry run: builds and checks the channel, uploads nothing.
CHEF dryrun --thumbnails manifest=/w/manifest.csv videos=/w/h264_480p topics=/w/topics.csv band=G4-5
CHEF dryrun --thumbnails manifest=/w/manifest.csv videos=/w/h264_480p topics=/w/topics.csv band=G6-7
```

A dry run stops with a list of problems if any row for the band lacks a
licence, copyright holder or file.

To upload, get a Studio API token (Studio → Settings → API Token) and save it
in a file **outside** any repository, readable only by you:
`~/.studio_token`, then `chmod 600 ~/.studio_token`. Mount it read-only for
the upload, and the token never appears on the command line:

```sh
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/run \
  -v "$PWD":/code:ro -v ~/.studio_token:/secrets/studio_token:ro \
  -v "$W":/w -v "$W/chef":/run -w /run lshe-content-tools \
  python /code/lshe_video_chef.py --token=/secrets/studio_token --thumbnails \
  manifest=/w/manifest.csv videos=/w/h264_480p topics=/w/topics.csv band=G4-5
```

Run it again with `band=G6-7` for the second channel.

The channel is uploaded as a draft. Open it in Studio, check the titles,
order and playback, then **publish** it. Re-running the chef updates the
same channel: each video's id is its code, so nothing is duplicated. Each
band is a separate channel (`LSHE G4-5 videos`, `LSHE G6-7 videos`),
because LSHE grade bands are set per channel.

### Export from Studio to disk

On a Kolibri on the workstation (any Kolibri 0.16 with internet):

1. **Device → Channels → Import → Kolibri Studio**, and import each published
   channel by its token or from your channels.
2. **Device → Channels → Export**, choose a folder (for example a USB drive),
   and export each channel with all its content. With commands:
   `kolibri manage exportchannel <channel_id> <folder>` and
   `kolibri manage exportcontent <channel_id> <folder>`.

Put the channel ids in a `channels.conf` (see `../channels.conf.example`),
then import as in section 4.

## Data protection

The videos and manifest hold no learner data. Never put a Studio token,
`.env` or any learner data in this folder or in a manifest. `.gitignore`
keeps out `studio_token*` and ricecooker's working folders.
