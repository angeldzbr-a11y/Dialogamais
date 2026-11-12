import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("dialoga.store")
DATABASE_URL = os.getenv("DATABASE_URL", "")
STATE_FILE = os.getenv("STATE_FILE", "")
DIALETO = "postgres"  # os testes usam "sqlite"
SCHEMA = Path(__file__).with_name("schema.sql")
_schema_pronto = False


def ativo():
    return bool(DATABASE_URL or STATE_FILE)


def modo():
    return "postgres" if DATABASE_URL else ("arquivo" if STATE_FILE else "memoria")


# conexão e utilidades
def _conectar():
    import psycopg2  # importado aqui para a API subir mesmo sem o driver (modo memória)
    return psycopg2.connect(DATABASE_URL, connect_timeout=10)


def _script(cur, sql):
    cur.execute(sql)


def _tabela_existe(cur, nome):
    if DIALETO == "sqlite":
        cur.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = %s", (nome,))
    else:
        cur.execute("SELECT to_regclass(%s)", (nome,))
    row = cur.fetchone()
    return bool(row and row[0])


def _preparar(cur):
    """Cria as tabelas que faltam. A marca _schema_pronto só vale depois do commit (quem chama a define)."""
    if not _schema_pronto:
        _script(cur, SCHEMA.read_text(encoding="utf-8"))


def _ts(valor):
    """Texto ISO -> datetime com fuso (UTC se não tiver). Valor inválido vira 'agora' para não derrubar a gravação."""
    try:
        d = datetime.fromisoformat(str(valor))
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _iso(valor):
    if isinstance(valor, datetime):
        return (valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()
    return None if valor is None else str(valor)


def _json(valor):
    return json.loads(valor) if isinstance(valor, (str, bytes)) else valor


def _dump(valor):
    return json.dumps(valor, ensure_ascii=False)


def _inserir(cur, tabela, colunas, linhas):
    if linhas:
        cur.executemany(f"INSERT INTO {tabela} ({', '.join(colunas)}) VALUES ({', '.join(['%s'] * len(colunas))})", linhas)


APAGAR_NA_ORDEM = ["sala_respostas", "sala_atividades", "cenarios_exclusivos", "atividades_exclusivas", "sala_membros",
                   "salas", "historico_respostas", "historico", "rascunhos", "progresso", "consentimentos", "usuarios"]


# gravar (memória -> tabelas)
def _gravar(cur, estado):
    omitidos = {}

    def pular(tabela):
        omitidos[tabela] = omitidos.get(tabela, 0) + 1

    cat = estado.get("catalogo")
    if cat:  # catálogo de temas e situações (vem do código)
        for t in ("cenarios", "temas"):
            cur.execute(f"DELETE FROM {t}")
        _inserir(cur, "temas", ["id", "nome"], [(t["id"], t["nome"]) for t in cat["temas"]])
        ordem, linhas = {}, []
        for c in cat["cenarios"]:
            k = (c["tema_id"], c["nivel"])
            ordem[k] = ordem.get(k, 0) + 1
            linhas.append((c["id"], c["tema_id"], c["nivel"], ordem[k], c["texto"], c["criterios"], bool(c["ativo"])))
        _inserir(cur, "cenarios", ["id", "tema_id", "nivel", "ordem", "texto", "criterios", "ativo"], linhas)
    cur.execute("SELECT id FROM temas")
    temas = {r[0] for r in cur.fetchall()}

    for t in APAGAR_NA_ORDEM:
        cur.execute(f"DELETE FROM {t}")

    users = estado.get("users", {})
    ids = {email: u["id"] for email, u in users.items()}
    _inserir(cur, "usuarios", ["id", "nome", "email", "senha_hash", "perfil", "menor", "criado_em"],
             [(u["id"], u["nome"], email, u["hash"], u.get("perfil", "aluno"), bool(u.get("menor")), _ts(u.get("criado")))
              for email, u in users.items()])
    _inserir(cur, "consentimentos", ["usuario_id", "ordem", "tipo", "aceito", "versao", "data"],
             [(u["id"], i, c["tipo"], bool(c["aceito"]), c["versao"], _ts(c["data"]))
              for u in users.values() for i, c in enumerate(u.get("consents", []))])

    linhas = []
    for email, por_tema in estado.get("prog", {}).items():
        if email not in ids:
            pular("progresso"); continue
        for tid, niveis in por_tema.items():
            if tid not in temas:
                pular("progresso"); continue
            linhas += [(ids[email], tid, n, i) for i, n in enumerate(niveis)]
    _inserir(cur, "progresso", ["usuario_id", "tema_id", "nivel", "ordem"], linhas)

    linhas = []
    for email, d in estado.get("drafts", {}).items():
        if email in ids and d.get("theme_id") in temas:
            linhas.append((ids[email], d["theme_id"], d.get("indice", 0), _dump(d.get("answers", [])), _ts(d.get("atualizado"))))
        else:
            pular("rascunhos")
    _inserir(cur, "rascunhos", ["usuario_id", "tema_id", "indice", "respostas", "atualizado_em"], linhas)

    hist, resp = [], []
    for email, itens in estado.get("hist", {}).items():
        if email not in ids:
            pular("historico"); continue
        for pos, h in enumerate(itens):
            tid = h.get("theme_id")
            hist.append((h["id"], ids[email], pos, tid if tid in temas else None, h.get("tema", ""), h.get("nivel"),
                         h.get("status", "concluída"), _ts(h.get("data")), _dump(h.get("feedback", {}))))
            for i, (s, a) in enumerate(zip(h.get("scen", []), h.get("answers", []))):
                resp.append((h["id"], i, s["id"], s["texto"], a))
    _inserir(cur, "historico", ["id", "usuario_id", "ordem", "tema_id", "tema_nome", "nivel", "status", "concluida_em", "feedback"], hist)
    _inserir(cur, "historico_respostas", ["historico_id", "ordem", "cenario_id", "situacao", "resposta"], resp)

    salas, membros, excl, cens, ativs, ars = [], [], [], [], [], []
    for sid, s in estado.get("salas", {}).items():
        if s["prof"] not in ids:
            pular("salas"); continue
        salas.append((sid, s["nome"], s["codigo"], ids[s["prof"]], bool(s["aberta"]), s.get("contador", 0), _ts(s.get("criada"))))
        for email, m in s.get("membros", {}).items():
            if email in ids:
                membros.append((sid, ids[email], m["anon"], _ts(m.get("entrou"))))
            else:
                pular("sala_membros")
        for i, c in enumerate(s.get("custom", [])):
            excl.append((c["id"], sid, i, c["titulo"], bool(c.get("ativo", True)), _ts(c.get("criada"))))
            cens += [(x["id"], c["id"], j, x["texto"]) for j, x in enumerate(c.get("cenarios", []))]
        for pos, a in enumerate(s.get("atividades", [])):
            if a["aluno"] not in ids:
                pular("sala_atividades"); continue
            tid = a.get("tema")
            ativs.append((a["id"], sid, ids[a["aluno"]], pos, a["anon"], tid if tid in temas else None,
                          a.get("tema_nome", ""), a.get("nivel"), _ts(a.get("data")), _dump(a.get("feedback", {}))))
            ars += [(a["id"], j, r["situacao"], r["resposta"]) for j, r in enumerate(a.get("respostas", []))]
    _inserir(cur, "salas", ["id", "nome", "codigo", "professor_id", "aberta", "contador", "criada_em"], salas)
    _inserir(cur, "sala_membros", ["sala_id", "usuario_id", "anon", "entrou_em"], membros)
    _inserir(cur, "atividades_exclusivas", ["id", "sala_id", "ordem", "titulo", "ativo", "criada_em"], excl)
    _inserir(cur, "cenarios_exclusivos", ["id", "atividade_id", "ordem", "texto"], cens)
    _inserir(cur, "sala_atividades", ["id", "sala_id", "usuario_id", "ordem", "anon", "tema_id", "tema_nome", "nivel", "feita_em", "feedback"], ativs)
    _inserir(cur, "sala_respostas", ["atividade_id", "ordem", "situacao", "resposta"], ars)
    if omitidos:  # linhas órfãs não derrubam a gravação, mas ficam registradas
        log.warning("Linhas ignoradas por não terem dono: %s", omitidos)


# ler (tabelas -> memória)
def _ler(cur):
    est = {"users": {}, "prog": {}, "salas": {}, "hist": {}, "drafts": {}}
    cur.execute("SELECT id, nome, email, senha_hash, perfil, menor, criado_em FROM usuarios")
    por_id = {}
    for i, nome, email, h, perfil, menor, criado in cur.fetchall():
        est["users"][email] = {"id": i, "nome": nome, "email": email, "hash": h, "criado": _iso(criado),
                               "perfil": perfil, "menor": bool(menor), "consents": []}
        por_id[i] = email
    cur.execute("SELECT usuario_id, tipo, aceito, versao, data FROM consentimentos ORDER BY usuario_id, ordem")
    for uid, tipo, aceito, versao, data in cur.fetchall():
        est["users"][por_id[uid]]["consents"].append({"tipo": tipo, "aceito": bool(aceito), "versao": versao, "data": _iso(data)})

    cur.execute("SELECT usuario_id, tema_id, nivel FROM progresso ORDER BY usuario_id, tema_id, ordem")
    for uid, tid, nivel in cur.fetchall():
        est["prog"].setdefault(por_id[uid], {}).setdefault(tid, []).append(nivel)
    cur.execute("SELECT usuario_id, tema_id, indice, respostas, atualizado_em FROM rascunhos")
    for uid, tid, indice, respostas, atu in cur.fetchall():
        est["drafts"][por_id[uid]] = {"theme_id": tid, "indice": indice, "answers": _json(respostas), "atualizado": _iso(atu)}

    cur.execute("SELECT id, usuario_id, tema_id, tema_nome, nivel, status, concluida_em, feedback FROM historico ORDER BY usuario_id, ordem")
    itens = {}
    for hid, uid, tid, tnome, nivel, status, em, fb in cur.fetchall():
        itens[hid] = {"id": hid, "theme_id": tid, "tema": tnome, "nivel": nivel, "data": _iso(em), "status": status,
                      "scen": [], "answers": [], "feedback": _json(fb)}
        est["hist"].setdefault(por_id[uid], []).append(itens[hid])
    cur.execute("SELECT historico_id, cenario_id, situacao, resposta FROM historico_respostas ORDER BY historico_id, ordem")
    for hid, cid, sit, r in cur.fetchall():
        itens[hid]["scen"].append({"id": cid, "texto": sit})
        itens[hid]["answers"].append(r)

    cur.execute("SELECT id, nome, codigo, professor_id, aberta, contador, criada_em FROM salas")
    for sid, nome, codigo, pid, aberta, cont, criada in cur.fetchall():
        est["salas"][sid] = {"id": sid, "nome": nome, "codigo": codigo, "prof": por_id[pid], "aberta": bool(aberta),
                             "criada": _iso(criada), "membros": {}, "atividades": [], "contador": cont, "custom": []}
    cur.execute("SELECT sala_id, usuario_id, anon, entrou_em FROM sala_membros")
    for sid, uid, anon, entrou in cur.fetchall():
        est["salas"][sid]["membros"][por_id[uid]] = {"entrou": _iso(entrou), "anon": anon}
    cur.execute("SELECT id, sala_id, titulo, ativo, criada_em FROM atividades_exclusivas ORDER BY sala_id, ordem")
    custom = {}
    for cid, sid, titulo, ativo, criada in cur.fetchall():
        custom[cid] = {"id": cid, "titulo": titulo, "ativo": bool(ativo), "criada": _iso(criada), "cenarios": []}
        est["salas"][sid]["custom"].append(custom[cid])
    cur.execute("SELECT id, atividade_id, texto FROM cenarios_exclusivos ORDER BY atividade_id, ordem")
    for xid, cid, texto in cur.fetchall():
        custom[cid]["cenarios"].append({"id": xid, "texto": texto})
    cur.execute("SELECT id, sala_id, usuario_id, anon, tema_id, tema_nome, nivel, feita_em, feedback FROM sala_atividades ORDER BY sala_id, ordem")
    ativs = {}
    for aid, sid, uid, anon, tid, tnome, nivel, em, fb in cur.fetchall():
        ativs[aid] = {"id": aid, "aluno": por_id[uid], "anon": anon, "tema": tid, "tema_nome": tnome, "nivel": nivel,
                      "data": _iso(em), "feedback": _json(fb), "respostas": []}
        est["salas"][sid]["atividades"].append(ativs[aid])
    cur.execute("SELECT atividade_id, situacao, resposta FROM sala_respostas ORDER BY atividade_id, ordem")
    for aid, sit, r in cur.fetchall():
        ativs[aid]["respostas"].append({"situacao": sit, "resposta": r})
    return est


def _contar(cur, tabela):
    cur.execute(f"SELECT COUNT(*) FROM {tabela}")
    return cur.fetchone()[0]


# API pública
def carregar():
    """Devolve o último estado salvo (dict) ou None se ainda não existe. Levanta erro se o banco estiver inacessível:
    assim a API não sobe vazia e não sobrescreve os dados existentes."""
    global _schema_pronto
    if DATABASE_URL:
        cn = _conectar()
        try:
            cur = cn.cursor()
            _preparar(cur)
            cn.commit()
            _schema_pronto = True
            if _contar(cur, "usuarios") or _contar(cur, "salas"):
                return _ler(cur)
            if _tabela_existe(cur, "dialoga_estado"):  # migração do documento JSON da versão anterior
                cur.execute("SELECT dados FROM dialoga_estado WHERE id = 1")
                row = cur.fetchone()
                if row:
                    log.warning("Migrando o documento JSON antigo para tabelas relacionais.")
                    return _json(row[0])
            return None
        finally:
            cn.close()
    if STATE_FILE and os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return None


def salvar(estado):
    """Grava o estado completo. Nunca levanta erro: falha de gravação não derruba a requisição."""
    global _schema_pronto

    try:
        if DATABASE_URL:
            cn = _conectar()
            try:
                cur = cn.cursor()
                _preparar(cur)
                _gravar(cur, estado)
                if _tabela_existe(cur, "dialoga_estado"):
                    cur.execute("DROP TABLE dialoga_estado")  # evita que dados já excluídos voltem
                cn.commit()
                _schema_pronto = True
            except Exception:
                cn.rollback()
                raise
            finally:
                cn.close()
        elif STATE_FILE:
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(json.dumps(estado, ensure_ascii=False, default=str))
            os.replace(tmp, STATE_FILE)
        return True
    except Exception as e:
        log.error("Falha ao gravar o estado: %s", type(e).__name__)
        return False
