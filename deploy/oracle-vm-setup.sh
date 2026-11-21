#!/usr/bin/env bash
# One-time setup for an Oracle Cloud "Always Free" VM (Ubuntu 22.04/24.04, Ampere A1 or E2.1.Micro).
# Gives you a reserved public IP to whitelist with your broker for live API orders.
#
#   1. In OCI console: Networking -> Reserved public IPs -> reserve one, attach to the VM's VNIC.
#   2. Security list / NSG: allow TCP 22, 80, 443 inbound.
#   3. ssh ubuntu@<ip> 'bash -s' < deploy/oracle-vm-setup.sh
#   4. Copy the repo + .env to the VM, then: docker compose up -d --build
#   5. Put STATIC_IP=<reserved ip> in .env and register the same IP in your broker's API app.
set -euo pipefail

sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg ufw caddy || sudo apt-get install -y ca-certificates curl gnupg ufw
# Docker Engine + compose plugin
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"

# Ubuntu images on OCI ship iptables rules that block 80/443; open them.
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT || true
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT || true
sudo netfilter-persistent save 2>/dev/null || true

# Keep the clock right: candle-close scheduling depends on it.
sudo timedatectl set-timezone Asia/Kolkata
sudo timedatectl set-ntp true

echo "Public egress IP (whitelist this with your broker):"
curl -s https://api.ipify.org; echo
echo "Next: clone the repo, create .env, run 'docker compose up -d --build', and point Caddy at :8000 (see deploy/Caddyfile)."
