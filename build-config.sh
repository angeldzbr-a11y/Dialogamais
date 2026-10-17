#!/bin/sh
# Roda no build do Netlify: escreve docs/config.js com o endereco da API (variavel DIALOGA_API).
: "${DIALOGA_API:?Defina a variavel DIALOGA_API no Netlify, ex.: https://dialoga-api.onrender.com/api}"
case "$DIALOGA_API" in
  https://*/api) ;;
  *) echo "DIALOGA_API deve comecar com https:// e terminar em /api" >&2; exit 1 ;;
esac
printf '// Gerado no build do Netlify a partir da variavel DIALOGA_API.\nwindow.DIALOGA_API = "%s";\n' "$DIALOGA_API" > docs/config.js
echo "config.js gerado para $DIALOGA_API"
