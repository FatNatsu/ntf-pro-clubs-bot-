# Running NTF 24/7 on an Oracle Cloud free server

The bot only makes outgoing connections to Discord, so the server needs no
open ports, no domain and no web server. One small Linux machine is enough.

## 1. Create the server (you do this, in Oracle's website)

1. Sign up at Oracle Cloud. It asks for a card to verify you; the "Always
   Free" resources are not charged.
2. Create a **Compute instance**:
   - Image: **Ubuntu** (the newest LTS offered).
   - Shape: an **Always Free eligible** one. The ARM (Ampere) shape is the
     roomy one; the small AMD micro shape is also enough for this bot.
   - If it says "Out of host capacity", try another availability domain or
     try again later. That message is common on free accounts.
3. When asked about SSH keys, let Oracle **generate a key pair** and
   **download the private key**. Keep that file safe: it is the only way in.
4. Note the instance's **public IP address**.

## 2. Connect to it

From your PC (PowerShell), with the key file you downloaded:

```
ssh -i path\to\your-key.key ubuntu@YOUR_SERVER_IP
```

## 3. Install the bot

On the server:

```
sudo apt-get update -y
sudo apt-get install -y git
git clone https://github.com/FatNatsu/ntf-pro-clubs-bot-.git
cd ntf-pro-clubs-bot-
bash deploy/install.sh
```

Until the V2 work is merged into `main`, clone the V2 branch instead:

```
git clone -b match-channels https://github.com/FatNatsu/ntf-pro-clubs-bot-.git
```

## 4. Give it the token

The token is never stored in this repo. Put it in the settings file the
install script created:

```
sudo nano /etc/ntf-bot.env
```

- `DISCORD_BOT_TOKEN` - the bot's token from the Discord Developer Portal.
- `DISCORD_GUILD_IDS` - optional, comma-separated server IDs that should get
  slash commands instantly.
- `PRO_CLUBS_DB_PATH` - where player data is saved. Already filled in; it
  points outside the repo so updates never touch it.

Save (Ctrl+O, Enter) and exit (Ctrl+X). Never paste the token into a chat.

## 5. Start it

The database is created empty the first time the bot starts (that is the V2
reset). After it's online: run `/ntf_setup` in your server, give your admin
role access to the admin-log channel, and add your clubs with `/club add`.


```
sudo systemctl start ntf-bot
sudo journalctl -u ntf-bot -f
```

The log should show the bot logging in. Press Ctrl+C to stop watching; the
bot keeps running. It restarts by itself after a crash or a reboot.

## Day to day

| What | Command |
|---|---|
| Is it running? | `sudo systemctl status ntf-bot` |
| Watch the log | `sudo journalctl -u ntf-bot -f` |
| Restart | `sudo systemctl restart ntf-bot` |
| Stop | `sudo systemctl stop ntf-bot` |

### Updating to new code

```
cd ~/ntf-pro-clubs-bot-
git pull
.venv/bin/pip install -r requirements.txt
sudo systemctl restart ntf-bot
```

Restarting ends any session in progress (live session state is kept in
memory), so update between sessions. Reported results, MMR and history are
in the database and are not affected.

### Backing up player data

Everything is one file: `~/ntf-data/pro_clubs.db`.

**Automatic backups.** The bot takes a backup by itself before every season
reset. For a daily backup as well, install the timer once (after updating to
a version that has `backup.py`):

```
sudo cp deploy/ntf-backup.service deploy/ntf-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ntf-backup.timer
```

Backups go to `~/ntf-data/backups/`, are checked after they're written, and
the newest 30 of each kind are kept (set `NTF_BACKUP_KEEP` in
`/etc/ntf-bot.env` to change it). Take one by hand any time with
`.venv/bin/python backup.py --verify`.

**Restoring a backup** (only if something went badly wrong):

```
sudo systemctl stop ntf-bot
cp ~/ntf-data/pro_clubs.db ~/ntf-data/pro_clubs.before-restore.db
cp ~/ntf-data/backups/THE-BACKUP-FILE.db ~/ntf-data/pro_clubs.db
sudo systemctl start ntf-bot
```

The backups live on the same server, so also copy one to your PC now and
then, for example:

```
scp -i path\to\your-key.key ubuntu@YOUR_SERVER_IP:ntf-data/pro_clubs.db .
```

## Only one copy of the bot may run

If the same token is running anywhere else (Railway, your PC), both copies
answer every button press and things break. Make sure the old host is off
before starting this one.
