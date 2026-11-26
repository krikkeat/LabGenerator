# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
An internal team (presales / solution engineers) who spin up AWS environments for customer PoCs and hands-on sessions, such as `poc-bgc`. They run labctl locally on their own Mac, usually shortly before or during a customer session, and adjust live: add or remove a VM, reset one, extend time, hand out access files.

## Product Purpose
labctl turns "I need N machines of OS X for customer Y" into running EC2 VMs with per-person access packs (handout zip with IP, credentials, key, guide) in minutes, then tears everything down cleanly so nothing keeps billing. Success: from login to a working VM with no AWS console visit, and no orphaned resources afterward.

## Positioning
A local, single-operator tool that owns the whole lifecycle of a throwaway AWS environment: one isolated VPC per lab managed by Terraform, per-person access packs, auto-expiry, and per-VM add/reset/remove without touching the others.

## Operating Context
- Runs as a local FastAPI server (`labctl.py serve`, port 8787) opened in the browser; a CLI (`labctl.py`) covers the same jobs.
- AWS access comes from profiles in `~/.aws`: SSO profiles signed in through the browser are the primary path; pasted access keys (temporary portal keys or static) are the fallback. Several accounts may be in use; each lab belongs to the account it was created in.
- Long-running work (terraform apply/destroy) runs as jobs with a live log.
- Region default ap-southeast-1. Thai is the UI language; AWS/technical terms stay in English.

## Capabilities and Constraints
- Create a lab: product preset or OS (Amazon Linux, Ubuntu, Debian, SUSE, Windows Server 2016–2025, or custom AMI), instance type, users list, TTL, spot, extra ports, lab guide (Markdown/PDF).
- Per lab: add VMs (count or names, optional different OS), reset or remove a single VM, +hours, stop/start, refresh, export handouts, share handouts over LAN, destroy.
- Windows passwords arrive ~4–5 min after boot; a watcher regenerates handouts.
- Orphan scanner finds tagged resources whose Terraform state was lost.
- App login is a local account (single operator per machine).

## Evidence on Hand
Real labs on the operator's AWS account only. No customer names, logos, testimonials or pricing exist in the repo; do not fabricate them.

## Product Principles
1. Never surprise-destroy: anything that deletes or replaces a VM says so first and touches only what it names.
2. Money is visible: running time, expiry and VM count are always in view.
3. Shortest path to a working VM: defaults first, everything else folded away.
4. One account, stated: the AWS account in use is always shown, and labs stay bound to their account.
