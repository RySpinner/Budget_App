# Deploying on the Raspberry Pi

Target: **Raspberry Pi 5, Raspberry Pi OS (64-bit)**. Everything below is typed in a
terminal **on the Pi** (over SSH or with a keyboard), unless a step says "on your
laptop". It takes about an hour the first time.

What you end up with:

| Piece | Where |
|---|---|
| App code + Python environment | `/opt/household-budget` (owned by the `budget` system user) |
| Secrets (DB password, login password hash) | `/opt/household-budget/config/secrets.toml` (readable only by `budget`) |
| Archived original CSVs | `/var/lib/household-budget/archive` |
| Nightly dumps | `/var/backups/household-budget` (+ a copy on your Mac / Linux machine / NAS) |
| The app | `http://<pi-name>.local:8501`, started automatically at boot |

---

## 1. Prepare the Pi

1. Flash **Raspberry Pi OS (64-bit)** with Raspberry Pi Imager. In the Imager's settings:
   - set a hostname, e.g. `budget-pi`;
   - create your user;
   - turn on SSH.

   SPEC recommends booting from a **USB SSD** rather than an SD card, because SD cards wear out.
2. In your router, give the Pi a fixed address (a "DHCP reservation") so it's always at the same address.
3. Update it and install what's needed:
   ```bash
   sudo apt update && sudo apt full-upgrade -y
   sudo apt install -y postgresql python3-venv git rsync
   psql --version        # must be 15 or newer
   ```

## 2. Database

Postgres on Raspberry Pi OS only accepts connections from the Pi itself by default, which is what we want. Check:
```bash
sudo -u postgres psql -c "SHOW listen_addresses;"     # should say: localhost
```
Create the app's own user and database. Choose a long random password and write it down for step 4:
```bash
sudo -u postgres psql -c "CREATE ROLE budget_app LOGIN PASSWORD 'CHANGE-ME';"
sudo -u postgres createdb --owner=budget_app household_budget
```

## 3. App user and code

```bash
sudo useradd --system --create-home --home-dir /var/lib/household-budget --shell /usr/sbin/nologin budget
sudo mkdir -p /opt/household-budget /var/lib/household-budget/archive /var/backups/household-budget
sudo chown budget:budget /opt/household-budget /var/lib/household-budget/archive /var/backups/household-budget
sudo -u budget git clone https://github.com/RySpinner/Budget_App.git /opt/household-budget
cd /opt/household-budget
sudo -u budget python3 -m venv .venv
sudo -u budget .venv/bin/pip install -r requirements.txt
```
If the repository is private, `git clone` asks for a username and password. Use your GitHub username and a **personal access token** (GitHub → Settings → Developer settings → Fine-grained tokens, with read access to this repo) as the password.

## 4. Secrets

```bash
cd /opt/household-budget
sudo -u budget cp config/secrets.example.toml config/secrets.toml
sudo -u budget chmod 600 config/secrets.toml
sudo -u budget .venv/bin/python -m budget.auth        # choose the shared login password
sudo -u budget nano config/secrets.toml
```
In the editor:
- `[database]`: set `password` to the database password from step 2.
- `[paths]`: set `archive_dir = "/var/lib/household-budget/archive"`.
- `[app]`: replace it with the two lines `python -m budget.auth` printed (`password_hash` and `cookie_secret`).

## 5. Tables and starting data

Copy your `seed/local.toml` (people and cards) from your laptop to the Pi.

On your laptop (PowerShell or Mac/Linux terminal, from the project folder):
```powershell
scp seed/local.toml <your-pi-user>@budget-pi.local:/tmp/local.toml
```
Then on the Pi:
```bash
cd /opt/household-budget
sudo install -o budget -g budget -m 600 /tmp/local.toml seed/local.toml && rm /tmp/local.toml
sudo -u budget .venv/bin/python -m budget.migrate
sudo -u budget .venv/bin/python -m budget.seed
```

## 6. Start the app at boot

```bash
sudo cp /opt/household-budget/deploy/household-budget.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now household-budget
systemctl status household-budget          # should say "active (running)"
```
Open **http://budget-pi.local:8501** from any device at home. If `.local` names don't work on a device, use the Pi's IP address. Log in with the shared password. Tick "Remember this browser" to stay logged in for 30 days.

## 7. Nightly backups (to your Mac / Linux machine / NAS)

**On the receiving machine:** turn on SSH and create a folder for the backups.
- *Mac:* System Settings → General → Sharing → **Remote Login** on. Folder e.g. `/Users/<you>/Backups/household-budget`.
- *Synology / other NAS:* enable **SSH** (and rsync, if it has a separate switch). Folder e.g. `/volume1/backups/household-budget`.
- *Linux:* `sudo apt install openssh-server rsync`, then `mkdir -p ~/backups/household-budget`.

**On the Pi:**
1. Database password for the backup job (it never goes in the backup config):
   ```bash
   echo "localhost:5432:household_budget:budget_app:CHANGE-ME" | sudo -u budget tee /var/lib/household-budget/.pgpass > /dev/null
   sudo -u budget chmod 600 /var/lib/household-budget/.pgpass
   ```
2. An SSH key that lets the Pi log in to the receiving machine without a password:
   ```bash
   sudo -u budget mkdir -p -m 700 /var/lib/household-budget/.ssh
   sudo -u budget ssh-keygen -t ed25519 -N "" -f /var/lib/household-budget/.ssh/backup_key
   sudo -u budget ssh-copy-id -i /var/lib/household-budget/.ssh/backup_key.pub <you>@<backup-machine>
   ```
   `ssh-copy-id` asks for your password on the receiving machine once.
3. Backup settings:
   ```bash
   sudo mkdir -p /etc/household-budget
   sudo cp /opt/household-budget/deploy/backup.env.example /etc/household-budget/backup.env
   sudo nano /etc/household-budget/backup.env      # set REMOTE=<you>@<backup-machine>:/path/to/folder
   ```
4. Run one backup by hand and check it worked:
   ```bash
   sudo cp /opt/household-budget/deploy/household-budget-backup.{service,timer} /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl start household-budget-backup
   journalctl -u household-budget-backup -n 30      # should end with "[backup] done"
   ```
   On the receiving machine you should now see `db/daily/…dump`, `db/monthly/…dump` and `csv-archive/`.
5. Turn on the nightly schedule (about 2:30 am; a run missed while the Pi was off happens after it boots):
   ```bash
   sudo systemctl enable --now household-budget-backup.timer
   systemctl list-timers household-budget-backup.timer
   ```

**Retention:** 14 daily dumps and 12 monthly dumps, on the Pi and mirrored to the remote. The original CSVs are only ever added to the remote copy, never deleted. You can change these numbers in `backup.env`.

**Is it still working?** `systemctl status household-budget-backup` shows the last run. A failed run, including a failed copy to the remote, shows up as **failed**.

## 8. Test that a backup can be restored

Do this once now, then every month or two. It restores the newest dump into a scratch database, compares it with the live one, and deletes the scratch copy. Your real data is not touched.
```bash
sudo /opt/household-budget/deploy/restore-test.sh
```
It should end with `Restore test PASSED`.

## 9. Restoring for real

If the database is damaged or something was deleted by mistake:
```bash
ls /var/backups/household-budget/daily/                 # pick a dump
sudo /opt/household-budget/deploy/restore.sh /var/backups/household-budget/daily/household_budget-YYYY-MM-DD.dump
```
It asks you to type the database name, stops the app, replaces the database with the dump, and starts the app again. Anything entered after that backup is lost.

**If the Pi itself died:** set up a new Pi with steps 1–4 and 6, skipping `migrate` and `seed`. Copy a dump back from the backup machine and run `restore.sh` with it. Also copy `csv-archive/` back into `/var/lib/household-budget/archive`.

## 10. Updating to a new version

```bash
cd /opt/household-budget
sudo -u budget git pull
sudo -u budget .venv/bin/pip install -r requirements.txt
sudo -u budget .venv/bin/python -m budget.migrate
sudo systemctl restart household-budget
```

## 11. Changing the login password

```bash
cd /opt/household-budget
sudo -u budget .venv/bin/python -m budget.auth
sudo -u budget nano config/secrets.toml       # replace the [app] lines
sudo systemctl restart household-budget
```
Everyone is logged out and needs the new password.

## 12. Optional: access away from home (Tailscale)

Not set up by default. **Never** forward port 8501 on your router: that would put the app on the public internet. If you decide you want access away from home:
1. On the Pi:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   ```
   It prints a login link.
2. Install Tailscale on your phone/laptop and sign in with the same account.
3. Open `http://budget-pi:8501`, using the Pi's name as shown in the Tailscale app.

Only devices signed in to your Tailscale account can reach it, and the app password still applies.

## Troubleshooting

- **App not loading:** `systemctl status household-budget` and `journalctl -u household-budget -n 50`.
- **"No password is set yet":** the `[app]` lines are missing from `config/secrets.toml` (step 4).
- **Backup failed at the copy step:** test the SSH login as the backup user:
  ```bash
  sudo -u budget ssh -i /var/lib/household-budget/.ssh/backup_key <you>@<backup-machine> echo ok
  ```
- **Backup failed at the dump step:** check `/var/lib/household-budget/.pgpass` (exact format, mode 600).
