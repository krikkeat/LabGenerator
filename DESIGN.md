# labctl — Design

World: **Event Badge Desk**. A lab is the registration desk; every VM is one person's badge. Operate mode: the world lends palette, type, density and one signature move (the badge); layout, navigation and controls stay standard web components.

## Palette (tokens in `frontend/styles.css`)

| Role | Light | Dark | Use |
|---|---|---|---|
| Rail (lanyard) | `#14233a` | `#0a1424` | Sidebar, login brand panel, summary header, step numbers |
| Ground | `#e9edf2` | `#0d121a` | Page background (cool desk, never cream) |
| Panel | `#ffffff` | `#161e2a` | Cards, badges, inputs |
| Accent | `#1f56c9` | `#6f9cff` | Primary actions, focus, selection |
| OS band — Windows | `#2259c4` | `#2a5fc9` | Badge head, OS option top band, lab mini-badge |
| OS band — Linux | `#b8501a` | `#b8501a` | same |
| OS band — custom AMI | `#536175` | `#4c5a6e` | same |
| State | good `#137a52`, warn `#9a5b00`/line `#e6a23c`, crit `#b42f25` | lighter equivalents | Only where state is meant |

White text on every OS band meets 4.5:1.

## Type

IBM Plex Sans Thai for everything (Thai coverage is the reason). IBM Plex Mono only for data: IPs, usernames, lab names, account IDs, costs. Scale: page title 26/700, lab title 24/700, badge name 24/700, block title 15/700, body 14/400, help 12.5.

## Signature move — the badge

`.badge`: band head in the OS colour carrying OS name + protocol · person's name large · fine print rows (IP, username, password) in mono · dashed tear line, then actions (handout, reset, remove). All badges share one size and baseline in `.mac-grid`. A dashed "+ เพิ่มเครื่อง" ghost badge closes the row.

State is carried by the line under the band, not hue alone: solid green = ready, marching dashes = booting / waiting for Windows password, faded red = unreachable — always paired with a text status line.

## Time

`TimeBar`: remaining auto-expire time drawn as length (full = whole TTL), green → amber under 30 min → red when due. Appears on lab rows and in the lab header next to +1/+2 hr.

## Components

Buttons: primary (accent fill), ghost (bordered), danger (red text, fills red only in confirm dialogs), tiny (badge footers). Segmented control `.seg` for mode switches. Stepper for counts. OS picker `.os-opt` = mini badge with top band. Modals for destructive or multi-field actions only.

## Don'ts

No left-edge colour stripes on cards, no emoji/glyph icons (inline SVG set `ICON_PATHS`, stroke 1.75), no gradients as decoration, no cream ground.
