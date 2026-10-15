# Deploy on Freestyle

The deployment target is a Linux VM running this repository's Docker Compose stack.
Provisioning and live delivery are separate operations: the scripts do not purchase a
plan, create a GitHub token, or create a Telegram bot.

## Choose a durable VM

Freestyle's detailed documentation and pricing page currently disagree about persistent
VM availability on Free. Confirm the effective retention policy shown by your account
before storing your only copy of n8n credentials or the database. A Docker volume cannot
survive deletion of the VM that contains it.

The provisioner requests `freestyle/ubuntu-sm`, currently 2 vCPU, 4 GiB RAM, and 16 GB
disk, with a ten-minute network idle timeout. The free allowance is currently 200
allocated vCPU-hours and 400 GiB-hours per month; that shape permits about 100 running
hours if no other VM uses the pool. Allocation counts even while the application is
waiting. This is a demo budget, not a full month of continuous service.

If your plan rejects the smaller image or indefinite retention, inspect the account's
available options instead of automatically upgrading. The default larger Ubuntu image
uses 4 vCPU and 8 GiB RAM, consuming the same allowances in about 50 running hours.

## Provision the VM and HTTPS names

Requirements on your workstation: Node.js 24+ and pnpm. Obtain a Freestyle API key from
your account. Supply it through a private environment variable, never a repository file.

```bash
pnpm --dir deploy/freestyle install --frozen-lockfile
```

Set `FREESTYLE_API_KEY` privately and choose an available prefix. Then run:

```bash
FREESTYLE_PREFIX=ci-armin pnpm --dir deploy/freestyle provision
```

The script prints a VM ID and routes:

| Host | VM port | Service |
|---|---:|---|
| `ci-armin-hooks.style.dev` | 8000 | Signed GitHub webhook gateway |
| `ci-armin-n8n.style.dev` | 5678 | n8n editor and internal workflow webhook |

Freestyle supplies HTTPS for these names. No Caddy or Nginx container is needed in this
layout. The script creates outbound Internet access; PostgreSQL has no published host
port. It does not start the application yet.

Record the VM ID. If routing fails after creation, inspect the existing VM in the
dashboard and pass `FREESTYLE_VM_ID` on a retry to avoid allocating an additional VM.
Names are first-come-first-served; choose a different prefix if either name is taken.

## Install the application inside the VM

If you already created a VM in the dashboard, reuse it. You can run the application
installation in its **Terminal** without signing into Freestyle from another browser
or placing a Freestyle API key on the VM. The HTTPS routing rules still need to be
created separately in the dashboard or from an authenticated Freestyle CLI/SDK.

Open the VM terminal in the dashboard, or use the Freestyle CLI:

```bash
pnpm dlx freestyle@0.2.20 login
pnpm dlx freestyle@0.2.20 vm ssh YOUR_VM_ID
```

Inside the VM:

```bash
git clone https://github.com/aboumelon/n8n-ci-triage.git
cd n8n-ci-triage
python3 scripts/configure.py --public-url https://ci-armin-n8n.style.dev
```

Replace the hostname with your chosen n8n hostname. This generates secrets on the VM
and sets the following proxy configuration, without printing or overwriting secrets:

```dotenv
HOST_BIND=0.0.0.0
N8N_HOST=ci-armin-n8n.style.dev
N8N_PROTOCOL=https
N8N_PUBLIC_URL=https://ci-armin-n8n.style.dev
N8N_SECURE_COOKIE=true
```

Leave `N8N_DISPATCH_URL=http://n8n:5678/webhook/ci-triage`. This call stays inside the
Compose network. Keep demo mode for the first check, then:

```bash
docker compose up -d --build
```

On a VM where Docker requires root privileges, prefix Compose commands with `sudo`,
and use `sudo python3 scripts/bootstrap_n8n.py` for the import step. Do not make the
Docker socket world-writable.

Confirm local readiness before debugging public routes:

```bash
sudo docker compose ps
curl --fail http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:5678/healthz
```

1. Open the n8n HTTPS address and create the owner account promptly.
2. Run `python3 scripts/bootstrap_n8n.py` inside the repository in the VM.
3. Open **CI Triage**, review it, and Publish.
4. Send `python3 scripts/send_demo.py dependency` from the VM terminal.
5. Inspect the n8n execution and `python3 scripts/inspect_runs.py`.

For a real GitHub run, follow [github-and-telegram.md](github-and-telegram.md). The GitHub
webhook URL is `https://ci-armin-hooks.style.dev/webhooks/github`.

## Sleep, retries, and quotas

The gateway persists an event before acknowledging GitHub and then wakes n8n. If the
n8n endpoint is temporarily unavailable, the event stays pending. A once-per-minute
recovery trigger retries the inbox while the VM is running. When the whole VM is paused,
that schedule is paused too; another incoming webhook or an explicit VM start is needed.

Check idle behavior with the editor closed. Open browser connections and other network
activity can keep the VM awake. Verify that a real GitHub delivery wakes the gateway
and receives `202` within GitHub's response deadline. An exhausted monthly quota is a
different condition: automatic wake-up cannot override your plan's resource allowance.

Test restart persistence with `docker compose restart`, then verify the n8n account,
workflow, credentials, and saved reports still exist. Separately test pausing and
resuming the Freestyle VM. Do not use VM deletion as a persistence test.

## Backups and updates

The PostgreSQL volume contains both the n8n and triage databases. The n8n volume and the
`N8N_ENCRYPTION_KEY` are also required for a complete restore. Save backups outside the VM
using a destination you control. Do not commit database dumps, `.env`, decrypted n8n
credentials, or execution histories to the public repository.

The n8n image is pinned to `2.43.5`. Review upstream release notes, back up first, change
the pin deliberately, and run the demo after an upgrade. Updating the application uses
`git pull` followed by `docker compose up -d --build`.

## Primary references

- [Docker and Compose support](https://www.freestyle.sh/docs/guides/run-docker-in-a-sandbox)
- [Base image sizes](https://www.freestyle.sh/docs/vms/base-snapshots)
- [VM lifecycle and retention](https://www.freestyle.sh/docs/vms/lifecycle)
- [HTTPS domain routing](https://www.freestyle.sh/docs/vms/domains)
- [Pricing and limits](https://www.freestyle.sh/docs/vms/pricing-and-limits)
- [Pricing page](https://www.freestyle.sh/pricing)
- [n8n import commands](https://docs.n8n.io/hosting/cli-commands)
