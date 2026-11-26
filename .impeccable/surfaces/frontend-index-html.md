---
version: 1
slug: "frontend-index-html"
primary_target: "frontend/index.html"
related_targets: ["frontend/app.js","frontend/styles.css"]
---

# labctl app (frontend/index.html + app.js)

Mode: Operate. Users: presales engineers spinning up customer PoC VMs on their own Mac, often on a shared screen in a meeting room.
Job: log in → pick AWS account → create a lab in one short screen → hand out access → add/reset/remove VMs → destroy.
Constraints: Thai UI, English tech terms; preact+htm no build; light default (daytime meeting rooms, projectors), dark toggle kept.

## Direction contract
THESIS: Every VM is one person's badge from an event registration desk; the lab is the desk. Refuses the default cloud-console grid of grey tables with a blue accent.
OWN-WORLD: Deep lanyard navy (#14233a) rail; warm badge-stock ground (#f6f4ef) with white badge cards; OS colour bands (Windows blue #2f6fdc, Linux orange #e9762b, custom slate) on the badge head; state green #1f9d6b / amber / red only where meaningful. One humanist sans (IBM Plex Sans Thai already loaded for Thai) with tabular mono only for IPs, keys, ids.
STORY: The operator sees which AWS account is live, what is running and how long until it expires, and acts on one person without fear for the others.
FIRST VIEWPORT: Left navy rail: brand, Labs, New lab, AWS account chip (name, account id, state). Work area: lab header (name, state pill, account, expiry bar whose length is the remaining time, actions). Below, a row of equal-size badges on one baseline: badge head band in OS colour carrying protocol, user name large, fine print IP/user/password, foot actions handout · reset · remove. New lab: one-column form, account + OS + count + name up top, advanced folded.
FORM: candidate 4 of 7 (event badge desk); raises: state by band form + text (emission rail), time as length (labanotation), equal plates on one baseline (botanical folio). seed:9862409c0d41f7ca
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
