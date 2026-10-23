#!/usr/bin/env bash
# =============================================================================
# preparar_servidor.sh — Prepara um servidor Ubuntu (24.04 LTS ou mais novo) para o Examina.
# Rodar UMA vez, como root, logo depois de criar a VPS:
#
#   bash scripts/preparar_servidor.sh
#
# Faz: fuso horário de Brasília, atualizações (inclusive as automáticas de segurança),
# Docker + Compose v2, Git, firewall (só SSH, 80 e 443) e 2 GB de swap (folga de memória na
# geração de PDFs). Pode rodar de novo sem problema.
#
# Docker: se o servidor já vem com ele (ex.: imagem da Hostinger com o Docker oficial,
# docker-ce), o que existe é mantido — trocar pelos pacotes do Ubuntu (docker.io) remove o
# oficial e conflita com o docker-compose-plugin. Sem Docker, instala o do Ubuntu.
# =============================================================================

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Rode como root: sudo bash scripts/preparar_servidor.sh" >&2
  exit 1
fi

. /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]]; then
  echo "Aviso: feito para Ubuntu; este sistema é ${PRETTY_NAME:-desconhecido}." >&2
fi

export DEBIAN_FRONTEND=noninteractive

echo "==> Fuso horário"
timedatectl set-timezone America/Sao_Paulo

echo "==> Atualizações do sistema"
apt-get update
apt-get -y upgrade
apt-get install -y git ufw unattended-upgrades cron openssl

echo "==> Docker"
if command -v docker >/dev/null 2>&1; then
  echo "Docker já instalado ($(docker --version)) — mantido."
  if ! docker compose version >/dev/null 2>&1; then
    apt-get install -y docker-compose-plugin || apt-get install -y docker-compose-v2
  fi
else
  apt-get install -y docker.io docker-compose-v2
fi
systemctl enable --now docker

echo "==> Firewall (SSH, HTTP e HTTPS)"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

echo "==> Swap de 2 GB"
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
else
  echo "Já existe swap — mantido."
fi

echo "==> Atualizações automáticas de segurança"
dpkg-reconfigure -f noninteractive unattended-upgrades

echo
docker --version
docker compose version
echo
echo "✅ Servidor pronto. Próximo passo: bash scripts/criar_env_producao.sh <domínio> <e-mail do admin>"
