# LSHE helper scripts

Scripts for running an LSHE (Life Skills and Health Education) centre
server: importing learners and classes from MindSpark, importing channels,
setting grade bands, and the monthly report. There are also two scripts for
collecting the reports centrally.

The centre server runs Edulution's Kolibri fork in Docker (`docker/lshe/` in
the Kolibri repository). That fork enforces the LSHE rules: grade bands, the
teacher channels learners can't see, and the content lockdown. It also stores
the bands and the monthly class record. These scripts only set things up and
read data: they contain none of the rules, and they don't work with an
unmodified Kolibri. The full procedures are in the Kolibri repository's
`docs/lshe/`.

## What runs where

The Python scripts need Edulution's Kolibri, so they run **inside the Kolibri
container**. The Docker set-up mounts this repository there, read-only, at
`/opt/kolibri_helper_scripts`. The shell scripts run **on the host** and call
into the container with `docker exec`.

| File | Runs on | Purpose |
| --- | --- | --- |
| `run_import.sh` | centre server (host) | MindSpark roster import: dry run, database backup, then import |
| `import_from_mindspark.py` | Kolibri container | Imports learners and their classes from MindSpark (read-only) |
| `import_channels.sh` | centre server (host) | Imports channels and their content, from disk or another Kolibri server |
| `channels.conf.example` | centre server | Template list of channel ids for `import_channels.sh --file` |
| `run_script.sh` | centre server (host) | Runs one of the Python scripts in the container |
| `set_band_channels.py` | Kolibri container | Sets the channels of a grade band |
| `set_class_band.py` | Kolibri container | Puts a class in a grade band; `--list` shows all bands |
| `run_monthly_report.sh` | centre server (host) | Writes the previous month's report |
| `lshe_report.py` | Kolibri container | The monthly report: aggregate figures only |
| `systemd/lshe-report.{service,timer}` | centre server (host) | Runs the report on the 1st at 02:00, Lusaka time |
| `backup_db.sh` | centre server (host) | Backs up the Kolibri database, checks the dump, keeps 14 days |
| `systemd/lshe-backup.{service,timer}` | centre server (host) | Runs the backup every night at 01:30, Lusaka time |
| `mindspark_class_probe.js` | MindSpark server | Shows how learners link to classes, without personal data |
| `central/collect_reports.sh` | central machine | Pulls the report files from every centre |
| `central/combine_reports.py` | central machine | One file per month: all-centre totals, missing centres |
| `central/centres.conf.example` | central machine | Template list of centres |
| `lshe_common.py` | Kolibri container | Shared start-up and messages for the Python scripts |
| `content/` | workstation | Makes the LSHE video channels: converts the videos, then builds the channels offline (or on Studio). See [`content/README.md`](content/README.md) |

Every shell script prints its usage with `-h`. Every Python script prints
its usage with `--help`, for example `./run_script.sh set_class_band.py --help`.

## Set-up on a centre server

Put this repository next to the Kolibri checkout. `/opt/lshe/kolibri_helper_scripts`
is the default path the Kolibri compose file mounts:

```sh
sudo git clone -b develop https://github.com/dbwitso/kolibri_helper_scripts.git /opt/lshe/kolibri_helper_scripts
sudo mkdir -p /srv/lshe-reports /srv/lshe-content /srv/lshe-backups /var/log/lshe
```

If you put the repository somewhere else, set `LSHE_HELPER_SCRIPTS_DIR` in the
Kolibri `docker/lshe/.env` to that path. Then (re)start the containers from the
Kolibri `docker/lshe/` folder, so the mount takes effect:

```sh
cd /opt/lshe/kolibri/docker/lshe && docker compose up -d
docker exec lshe-kolibri ls /opt/kolibri_helper_scripts/lshe   # the scripts are visible
```

To update the scripts, run `git pull` in `/opt/lshe/kolibri_helper_scripts`.
The mount shows the new files straight away, so no restart is needed.

Run the shell scripts as root (`sudo`). They write to `/var/log/lshe` and
`/srv/lshe-backups`, and use Docker.

## Order of first-time set-up

1. **Learners and classes:** `./run_import.sh --org-id <ORG>` (a dry run),
   then the same with `--apply`.
2. **Channels:** copy the channel packs to `/srv/lshe-content`, list the
   channel ids in `channels.conf`, then run
   `./import_channels.sh --file channels.conf`. The LSHE video channels
   are built on a workstation with `content/build_offline_channels.sh`,
   which writes the pack and its `channels.conf` together.
3. **Teacher channels:** in the Kolibri `docker/lshe/.env`, set
   `LSHE_TEACHER_CHANNEL_IDS=<id>,<id>,<id>`, then `docker compose up -d`.
4. **Grade bands:** set the channels of each band, then the band of each
   class:

   ```sh
   ./run_script.sh set_band_channels.py --band G4-5 <channel_id> [<channel_id> ...]
   ./run_script.sh set_band_channels.py --band G6-7 <channel_id> [<channel_id> ...]
   ./run_script.sh set_class_band.py --class "Grade 5 A" --band G4-5
   ./run_script.sh set_class_band.py --class "Grade 6 A" --band G6-7
   ./run_script.sh set_class_band.py --list
   ```

   A class with no band can't be given any lessons. `--list` shows such
   classes under "No band".
5. **Teachers:** create them by hand in Kolibri as class coaches. They are
   not imported.
6. **Monthly report timer:** see below.
7. **Nightly backup timer:** see below.

## Roster import (MindSpark)

```sh
./run_import.sh --org-id <ORG>                             # dry run: counts only
./run_import.sh --apply --org-id <ORG>                     # backup, then import
./run_import.sh --apply --org-id <ORG> --remove-inactive   # end of term
```

The import reads MindSpark's MongoDB with MindSpark's existing login, from
`MINDSPARK_MONGO_URI` in the Kolibri `docker/lshe/.env`. It never writes to
MindSpark. Don't create or change MindSpark users for it.

With `--apply`, the Kolibri database is backed up to `/srv/lshe-backups`
first. The run is logged to `/var/log/lshe/import.log`. The Kolibri
repository's `docs/lshe/roster-import.md` covers how learners are matched,
what the counts mean, and when to run each command.

To see how MindSpark links learners to classes (run on the MindSpark server
itself; mongosh asks for the password):

```sh
mongosh -u <user> --authenticationDatabase admin --quiet \
  --file mindspark_class_probe.js > class_probe.txt
```

The output has no learner personal data. Delete it once it has been read.

## Channels

```sh
cp channels.conf.example channels.conf      # fill in the channel ids
./import_channels.sh --file channels.conf   # from /srv/lshe-content
./import_channels.sh --network http://<other server>:8080 --file channels.conf
```

Each channel is imported, then all its content. Re-running is safe, and
anything already on the server is skipped. The log is in
`/var/log/lshe/channels.log`.

## Monthly report timer

```sh
sudo cp systemd/lshe-report.service systemd/lshe-report.timer /etc/systemd/system/
# edit ExecStart in lshe-report.service if this repository is not in /opt/lshe/kolibri_helper_scripts
sudo systemctl daemon-reload
sudo systemctl enable --now lshe-report.timer
systemctl list-timers lshe-report.timer
```

The timer fires at 02:00 **Lusaka time** (`OnCalendar=… Africa/Lusaka`),
whatever time zone the server's clock uses. With `Persistent=true`, a missed
run (the server was off at 02:00 on the 1st) happens at the next boot.

To run the report by hand, use `sudo ./run_monthly_report.sh [YYYY-MM]`.
Re-running a month overwrites its file with the same figures. The log is in
`/var/log/lshe/report.log`.

You can use cron instead of the timer. Set the time zone in the crontab,
because cron otherwise uses the server's zone:

```
CRON_TZ=Africa/Lusaka
0 2 1 * * /opt/lshe/kolibri_helper_scripts/lshe/run_monthly_report.sh
```

The figures and files are described in the Kolibri repository's
`docs/lshe/reporting.md`.

## Nightly database backup

```sh
sudo cp systemd/lshe-backup.service systemd/lshe-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lshe-backup.timer
systemctl list-timers lshe-backup.timer
sudo ./backup_db.sh        # one now, to check
```

Every night at 01:30 **Lusaka time** (`Persistent=true` catches up after a
night the server was off), `backup_db.sh` dumps the Kolibri database to
`/srv/lshe-backups/kolibri_<date>_nightly.dump`, checks that the dump can be
read back, and deletes nightly dumps older than 14 days
(`LSHE_BACKUP_KEEP_DAYS`). Dumps that `run_import.sh` takes before an import
are never deleted. It refuses to start with less than 1 GB free. The log is
in `/var/log/lshe/backup.log`.

The dumps hold learner data (children's): they stay in `/srv/lshe-backups`
(mode 700, files 600). A copy off the server protects against a failed disk;
copy them only to storage that is as safe, and never to a shared drive.
Restoring is in the Kolibri repository's `docs/lshe/deployment.md` (Backup and restore).

## Collecting reports centrally

### On each centre server: a read-only user

The central machine connects as `lshe-reports`, which can only read
`/srv/lshe-reports`:

```sh
sudo adduser --system --group --shell /bin/sh lshe-reports
sudo chgrp -R lshe-reports /srv/lshe-reports && sudo chmod -R g+rX,o-rwx /srv/lshe-reports
sudo install -d -m 700 -o lshe-reports -g lshe-reports ~lshe-reports/.ssh
# One line in ~lshe-reports/.ssh/authorized_keys (the central machine's public key):
#   command="rrsync -ro /srv/lshe-reports",restrict ssh-ed25519 AAAA... lshe-central
```

`rrsync` comes with rsync. On older Ubuntu releases it is at
`/usr/share/doc/rsync/scripts/rrsync`, and needs copying to `/usr/local/bin`.
With this key restriction, the user can't open a shell, write files, or read
anything outside `/srv/lshe-reports`.

### On the central machine

```sh
cd lshe/central
cp centres.conf.example centres.conf   # one line per centre
LSHE_SSH_KEY=~/.ssh/lshe_reports ./collect_reports.sh
python3 combine_reports.py --month 2027-03
```

`collect_reports.sh` skips any centre it can't reach and carries on with the
rest. It exits non-zero if any centre failed. `combine_reports.py` writes
`combined/lshe_ALL_<month>.csv` and `combined/lshe_ALL_<month>_status.json`,
and lists centres that are missing, invalid, or reported a partial month.

## Data protection

Learners are children, so:

- Commit nothing with personal data, credentials or real MongoDB addresses:
  no `.env`, roster exports, probe output or collected reports. The example
  files hold placeholders only, and `.gitignore` keeps out the local
  `centres.conf`, `channels.conf`, `collected/`, `combined/` and
  `class_probe.txt`.
- The report files are aggregate counts only: no names, usernames, birth
  years or per-learner rows.
- The import log lists usernames only for MindSpark staff accounts that an
  earlier run imported as learners.

## Tests

The Python scripts' tests are Django tests against Edulution's Kolibri. Run
them where that Kolibri is installed with its test requirements (Kolibri's
`requirements/test.txt`), plus `test/requirements.txt`:

```sh
pip install -r lshe/test/requirements.txt
cd lshe/test && pytest
```

The central scripts' tests need only Python 3:
`python3 -m pytest lshe/central/test_combine_reports.py`.
