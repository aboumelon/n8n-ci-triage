import { Freestyle } from 'freestyle';

if (!process.env.FREESTYLE_API_KEY) {
  throw new Error('Set FREESTYLE_API_KEY locally; do not commit it.');
}
const prefix = process.env.FREESTYLE_PREFIX;
if (!prefix || prefix.length < 3 || prefix.length > 39 || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(prefix)) {
  throw new Error('Set FREESTYLE_PREFIX to an available lowercase name, 3–39 characters.');
}

const freestyle = new Freestyle();
const existing = process.env.FREESTYLE_VM_ID;
let vmId = existing;
if (!vmId) {
  const created = await freestyle.vms.create({
    slug: prefix,
    snapshotId: 'freestyle/ubuntu-sm',
    idleTimeoutSeconds: 600,
    autoDeleteSeconds: -1,
    firewall: {
      rules: [{ action: 'allow', source: {}, destination: { public: true } }],
    },
  });
  vmId = created.vmId;
}
console.log(`VM ID: ${vmId}`);

const domains = [
  { domain: `${prefix}-hooks.style.dev`, port: 8000 },
  { domain: `${prefix}-n8n.style.dev`, port: 5678 },
];
for (const route of domains) {
  await freestyle.tls.rules.create({
    action: 'allow',
    domain: route.domain,
    source: { public: true },
    destination: { vmId, port: route.port },
  });
}

console.log(JSON.stringify({ vmId, domains }, null, 2));
console.log('VM and HTTPS routes prepared. Deploy Compose inside the VM using docs/freestyle.md.');
console.log('Review effective retention and usage limits in the dashboard before storing data.');
console.log('If a domain step failed, reuse FREESTYLE_VM_ID to avoid creating another VM.');
