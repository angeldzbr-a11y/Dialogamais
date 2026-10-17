"""Persistência do estado da API.

* Com DATABASE_URL (PostgreSQL): grava o estado em uma tabela (uma linha JSONB). Os dados sobrevivem a
  reinícios e novos deploys.
* Sem DATABASE_URL: se STATE_FILE existir, usa um arquivo JSON local; senão guarda só na memória (demonstração).

Rode a API com UM processo. Para muitos usuários, o próximo passo é trocar este arquivo por tabelas
relacionais (veja README).
"""
import json
import logging
import os

log = logging.getLogger("dialoga.store")
DATABASE_URL = os.getenv("DATABASE_URL", "")
STATE_FILE = os.getenv("STATE_FILE", "")


def ativo():
    return bool(DATABASE_URL or STATE_FILE)


def _pg():
    import psycopg2  # importado aqui para a API subir mesmo sem o driver (modo memória)
    cn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
    with cn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS dialoga_estado ("
                    "id SMALLINT PRIMARY KEY, dados JSONB NOT NULL, atualizado TIMESTAMPTZ NOT NULL DEFAULT now())")
    cn.commit()
    return cn


def modo():
    return "postgres" if DATABASE_URL else ("arquivo" if STATE_FILE else "memoria")


def carregar():
    """Devolve o último estado salvo (dict) ou None se ainda não existe. Levanta erro se o banco estiver
    inacessível: assim a API não sobe vazia e não sobrescreve os dados existentes."""
    if DATABASE_URL:
        cn = _pg()
        try:
            with cn.cursor() as cur:
                cur.execute("SELECT dados FROM dialoga_estado WHERE id = 1")
                row = cur.fetchone()
            if not row:
                return None
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])
        finally:
            cn.close()
    if STATE_FILE and os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return None


def salvar(estado):
    """Grava o estado completo. Nunca levanta erro: falha de gravação não derruba a requisição."""
    try:
        texto = json.dumps(estado, ensure_ascii=False, default=str)
        if DATABASE_URL:
            cn = _pg()
            try:
                with cn.cursor() as cur:
                    cur.execute("INSERT INTO dialoga_estado (id, dados) VALUES (1, %s::jsonb) "
                                "ON CONFLICT (id) DO UPDATE SET dados = EXCLUDED.dados, atualizado = now()", (texto,))
                cn.commit()
            finally:
                cn.close()
        elif STATE_FILE:
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(texto)
            os.replace(tmp, STATE_FILE)
        return True
    except Exception as e:
        log.error("Falha ao gravar o estado: %s", type(e).__name__)
        return False
