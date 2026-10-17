import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as A
import gemini
import privacidade as P
import store

c = TestClient(A.app)
SENHA = "senha1234"
RESP = "Eu percebi o que aconteceu, me senti mal e preciso de respeito. Pediria para pararem."


class IAFalsa:
    """Guarda o que seria enviado à IA e devolve um feedback fixo."""

    def __init__(self):
        self.chamadas = []

    async def __call__(self, tema, respostas):
        self.chamadas.append((tema, respostas))
        return {"resumo": "ok", "observacao": "o", "sentimento": "s", "necessidade": "n", "pedido": "p",
                "exemplo": "e", "pontos_positivos": ["a"], "reflexoes": ["b"]}


def cadastrar(email, perfil="aluno", ia=True, menor=False, extra=None):
    corpo = {"nome": email.split("@")[0], "email": email, "senha": SENHA, "aceitou_termos": True, "perfil": perfil}
    if perfil == "aluno":
        corpo.update(menor_de_idade=menor, autorizacao_responsavel=menor, aceitou_ia=ia)
    corpo.update(extra or {})
    r = c.post("/api/auth/register", json=corpo)
    assert r.status_code == 201, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def respostas(tema="paz", nivel=1, texto=RESP):
    ids = [s["id"] for s in A.ativos(tema, nivel)]
    return [{"scenario_id": i, "texto": "texto falso do navegador", "resposta": texto} for i in ids]


class Base(unittest.TestCase):
    def setUp(self):
        for d in (A.USERS, A.TOKENS, A.PROG, A.SALAS, A.HIST, A.DRAFTS, A.LIMITES):
            d.clear()
        self.ia = IAFalsa()
        p = patch.object(A, "chamar_ia", self.ia)
        p.start()
        self.addCleanup(p.stop)
        p2 = patch.object(store, "DATABASE_URL", "")
        p3 = patch.object(store, "STATE_FILE", "")
        p2.start(), p3.start()
        self.addCleanup(p2.stop)
        self.addCleanup(p3.stop)

    def analisar(self, h, **kw):
        corpo = {"theme_id": "paz", "nivel": 1, "answers": respostas()}
        corpo.update(kw)
        return c.post("/api/analyze", json=corpo, headers=h)


class TestCadastroEPrivacidade(Base):
    def test_saude_e_aviso_publico(self):
        assert c.get("/api/health").json()["status"] == "ok"
        with patch.object(P, "PLANO_PAGO", False):
            info = c.get("/api/privacy").json()
        self.assertEqual(info["provedor_ia"], "Google (Gemini API)")
        self.assertTrue(info["modo_demonstracao"])  # GEMINI_PLANO_PAGO não está ligado
        self.assertEqual(info["retencao_dias"], P.RETENCAO_DIAS)

    def test_regras_do_cadastro(self):
        base = {"nome": "Ana", "email": "ana@x.com", "senha": SENHA, "aceitou_termos": True, "perfil": "aluno"}
        ruim = [
            ({"senha": "1234567"}, "senha curta"),
            ({"aceitou_termos": False, "menor_de_idade": False}, "sem aceitar os termos"),
            ({}, "aluno sem informar a faixa de idade"),
            ({"menor_de_idade": True}, "menor sem responsável"),
        ]
        for extra, motivo in ruim:
            r = c.post("/api/auth/register", json={**base, **extra})
            self.assertEqual(r.status_code, 400, motivo)
        r = c.post("/api/auth/register", json={**base, "menor_de_idade": True, "autorizacao_responsavel": True})
        self.assertEqual(r.status_code, 201)
        u = r.json()["usuario"]
        self.assertTrue(u["menor"])
        self.assertFalse(u["autoriza_ia"])  # a IA é consentimento separado
        tipos = [x["tipo"] for x in A.USERS["ana@x.com"]["consents"]]
        self.assertEqual(tipos, ["termos", "responsavel"])
        self.assertTrue(all(x["versao"] == P.VERSAO for x in A.USERS["ana@x.com"]["consents"]))

    def test_professor_nao_precisa_de_idade_nem_ia(self):
        cadastrar("prof@x.com", "professor")
        self.assertFalse(A.USERS["prof@x.com"]["menor"])

    def test_anonimizar(self):
        t, n = P.anonimizar("Fale com ana.silva@mail.com ou (92) 98888-7777, CPF 123.456.789-09, "
                            "CEP 69000-000, veja https://x.com/a e @ana_s")
        self.assertEqual(n, 6, t)
        for lixo in ("@mail", "98888", "789-09", "69000", "https://", "@ana"):
            self.assertNotIn(lixo, t)
        self.assertEqual(P.anonimizar("Eu falaria com calma em 2026, na sala 12.")[1], 0)


class TestEnvioParaIA(Base):
    def test_exige_login(self):
        self.assertEqual(c.post("/api/analyze", json={"theme_id": "paz", "answers": respostas()}).status_code, 401)
        self.assertEqual(self.ia.chamadas, [])

    def test_exige_consentimento_e_respeita_revogacao(self):
        h = cadastrar("ana@x.com", ia=False)
        self.assertEqual(self.analisar(h).status_code, 403)
        self.assertEqual(self.ia.chamadas, [])  # nada foi enviado
        self.assertTrue(c.put("/api/me/consents/ia", json={"aceito": True}, headers=h).json()["autoriza_ia"])
        self.assertEqual(self.analisar(h).status_code, 200)
        self.assertFalse(c.put("/api/me/consents/ia", json={"aceito": False}, headers=h).json()["autoriza_ia"])
        self.assertEqual(self.analisar(h).status_code, 403)
        self.assertEqual(len(self.ia.chamadas), 1)

    def test_so_depois_das_4_respostas(self):
        h = cadastrar("ana@x.com")
        self.assertEqual(self.analisar(h, answers=respostas()[:3]).status_code, 400)
        self.assertEqual(self.analisar(h, answers=respostas(texto="curta")).status_code, 400)
        self.assertEqual(self.ia.chamadas, [])
        self.assertEqual(self.analisar(h).status_code, 200)
        self.assertEqual(len(self.ia.chamadas), 1)
        self.assertEqual(len(self.ia.chamadas[0][1]), 4)  # as 4 respostas juntas, em uma única chamada

    def test_textos_e_criterios_vem_do_servidor(self):
        h = cadastrar("ana@x.com")
        self.assertEqual(self.analisar(h).status_code, 200)
        enviado = self.ia.chamadas[0][1]
        oficiais = A.ativos("paz", 1)
        self.assertEqual([e["texto"] for e in enviado], [s["texto"] for s in oficiais])
        self.assertTrue(all("Observação" in e["criterios"] and "Foco desta situação" in e["criterios"] for e in enviado))
        falsos = [{**r, "scenario_id": "paz-99"} for r in respostas()]
        self.assertEqual(self.analisar(h, answers=falsos).status_code, 409)

    def test_dados_pessoais_saem_antes_do_envio(self):
        h = cadastrar("ana@x.com")
        texto = "Eu falaria com ele pelo zap (92) 98888-7777 ou pelo e-mail joao@mail.com e explicaria com calma."
        self.assertEqual(self.analisar(h, answers=respostas(texto=texto)).status_code, 200)
        for e in self.ia.chamadas[0][1]:
            self.assertNotIn("joao@mail.com", e["resposta"])
            self.assertNotIn("98888", e["resposta"])
            self.assertIn("[dado removido]", e["resposta"])
        self.assertIn("joao@mail.com", A.HIST["ana@x.com"][0]["answers"][0])  # o estudante vê o que escreveu

    def test_nenhum_dado_de_conta_vai_no_prompt(self):
        prompt = A.montar_prompt("Cultura de Paz", [{"scenario_id": "x", "texto": "t", "criterios": "k", "resposta": "r"}])
        self.assertNotIn("@", prompt)
        self.assertIn("<criterios>k</criterios>", prompt)

    def test_falha_da_ia_nao_perde_nem_registra(self):
        h = cadastrar("ana@x.com")

        async def quebra(tema, resp):
            raise gemini.IAErro("Gemini respondeu HTTP 503")
        with patch.object(A, "chamar_ia", quebra):
            r = self.analisar(h)
        self.assertEqual(r.status_code, 502)
        self.assertIn("não foram perdidas", r.json()["erro"])
        self.assertEqual(A.HIST.get("ana@x.com", []), [])
        self.assertEqual(self.analisar(h).status_code, 200)  # tentar de novo funciona

    def test_niveis(self):
        h = cadastrar("bia@x.com")
        self.assertEqual(c.get("/api/themes/paz/scenarios?nivel=2", headers=h).status_code, 403)
        self.assertEqual(self.analisar(h).status_code, 200)
        self.assertEqual(c.get("/api/themes/paz/scenarios?nivel=2", headers=h).status_code, 200)
        self.assertEqual(self.analisar(h, nivel=3).status_code, 409)  # respostas do nível 1 não servem ao nível 3
        self.assertEqual(self.analisar(h, nivel=3, answers=respostas("paz", 3)).status_code, 200)


class TestSalas(Base):
    def test_fluxo_da_sala_com_professor_anonimo(self):
        prof, prof2, aluno = cadastrar("prof@x.com", "professor"), cadastrar("prof2@x.com", "professor"), cadastrar("ana@x.com")
        self.assertEqual(c.post("/api/rooms", json={"nome": "intruso"}, headers=aluno).status_code, 403)
        sala = c.post("/api/rooms", json={"nome": "8A turma"}, headers=prof).json()
        self.assertEqual(c.post("/api/rooms/join", json={"codigo": sala["codigo"]}, headers=aluno).status_code, 200)
        textos = [f"Situação exclusiva número {i} criada pelo professor." for i in range(1, 5)]
        cid = c.post(f"/api/rooms/{sala['id']}/custom", json={"titulo": "Recreio", "cenarios": textos}, headers=prof).json()["id"]
        self.assertEqual(c.get(f"/api/rooms/{sala['id']}/custom", headers=prof2).status_code, 404)
        resp = [{"scenario_id": "x", "texto": "", "resposta": RESP}] * 4
        r = c.post("/api/analyze", json={"custom_id": cid, "room_id": sala["id"], "answers": resp}, headers=aluno)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual([x["texto"] for x in self.ia.chamadas[0][1]], textos)
        det = c.get(f"/api/rooms/{sala['id']}", headers=prof).json()
        self.assertEqual(det["alunos"][0]["nome"], "Estudante 1")
        at = c.get(f"/api/rooms/{sala['id']}/activities/{det['atividades'][0]['id']}", headers=prof).json()
        self.assertTrue(at["respostas"][0]["resposta"].startswith("Eu percebi"))
        self.assertEqual(c.get(f"/api/rooms/{sala['id']}/activities/{at['id']}", headers=prof2).status_code, 404)
        self.assertNotIn("ana@x.com", str(det) + str(at))


class TestDireitosDoTitular(Base):
    def test_exportar_e_excluir(self):
        h = cadastrar("ana@x.com")
        self.assertEqual(self.analisar(h).status_code, 200)
        exp = c.get("/api/me/export", headers=h).json()
        self.assertEqual(exp["conta"]["email"], "ana@x.com")
        self.assertEqual(len(exp["historico"]), 1)
        self.assertEqual([x["tipo"] for x in exp["consentimentos"]], ["termos", "ia"])
        texto = json.dumps(exp)
        self.assertNotIn(A.USERS["ana@x.com"]["hash"], texto)  # o hash da senha não sai do servidor
        self.assertNotIn("Bearer", texto)
        self.assertEqual(len(c.get("/api/me/history", headers=h).json()), 1)
        self.assertEqual(c.delete("/api/me", headers=h).status_code, 200)
        self.assertNotIn("ana@x.com", A.USERS)
        self.assertNotIn("ana@x.com", A.HIST)
        self.assertEqual(c.get("/api/me", headers=h).status_code, 401)

    def test_exclusao_remove_atividades_na_sala_do_professor(self):
        prof, aluno = cadastrar("prof@x.com", "professor"), cadastrar("ana@x.com")
        sala = c.post("/api/rooms", json={"nome": "8A turma"}, headers=prof).json()
        c.post("/api/rooms/join", json={"codigo": sala["codigo"]}, headers=aluno)
        self.analisar(aluno, room_id=sala["id"])
        self.assertEqual(c.get(f"/api/rooms/{sala['id']}", headers=prof).json()["total_atividades"], 1)
        c.delete("/api/me", headers=aluno)
        self.assertEqual(c.get(f"/api/rooms/{sala['id']}", headers=prof).json()["total_atividades"], 0)

    def test_retencao(self):
        antigo = (datetime.now(timezone.utc) - timedelta(days=P.RETENCAO_DIAS + 5)).isoformat()
        novo = datetime.now(timezone.utc).isoformat()
        A.HIST["a@x.com"] = [{"id": "1", "data": novo}, {"id": "2", "data": antigo}]
        A.DRAFTS["a@x.com"] = {"atualizado": (datetime.now(timezone.utc) - timedelta(days=P.RETENCAO_RASCUNHO_DIAS + 1)).isoformat()}
        A.SALAS["s"] = {"atividades": [{"data": antigo}, {"data": novo}]}
        self.assertEqual(A.purgar_vencidos(), 3)
        self.assertEqual([h["id"] for h in A.HIST["a@x.com"]], ["1"])
        self.assertEqual(len(A.SALAS["s"]["atividades"]), 1)
        self.assertNotIn("a@x.com", A.DRAFTS)


class TestPersistencia(Base):
    def test_dados_sobrevivem_ao_reinicio(self):
        with tempfile.TemporaryDirectory() as d, patch.object(store, "STATE_FILE", os.path.join(d, "estado.json")):
            h = cadastrar("ana@x.com")
            self.assertEqual(self.analisar(h).status_code, 200)
            self.assertTrue(os.path.exists(store.STATE_FILE))  # gravou sozinho depois das alterações
            for dic in (A.USERS, A.TOKENS, A.PROG, A.SALAS, A.HIST, A.DRAFTS):
                dic.clear()  # "reinício" do servidor
            A.restaurar_estado()
            r = c.post("/api/auth/login", json={"email": "ana@x.com", "senha": SENHA})
            self.assertEqual(r.status_code, 200)
            novo = {"Authorization": "Bearer " + r.json()["token"]}
            self.assertEqual(len(c.get("/api/me/history", headers=novo).json()), 1)
            self.assertTrue(r.json()["usuario"]["autoriza_ia"])  # o consentimento também foi guardado

    def test_banco_fora_do_ar_nao_sobe_vazio(self):
        with patch.object(store, "carregar", side_effect=OSError("sem banco")), patch.object(A.time, "sleep"):
            with self.assertRaises(RuntimeError):
                A.restaurar_estado()


class FakeResp:
    def __init__(self, status, dados=None):
        self.status_code, self._d = status, dados

    def json(self):
        if self._d is None:
            raise ValueError("sem json")
        return self._d


def ok(texto):
    return FakeResp(200, {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": texto}]}}]})


class FakeHTTP:
    def __init__(self, fila, log):
        self.fila, self.log = fila, log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, content=None):
        self.log.append({"url": url, "headers": headers, "body": json.loads(content)})
        item = self.fila.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class TestGemini(unittest.TestCase):
    def rodar(self, fila, **kw):
        log = []

        async def sem_espera(_):
            return None
        with patch.object(gemini, "novo_cliente", lambda t: FakeHTTP(fila, log)), patch.object(gemini.asyncio, "sleep", sem_espera):
            args = dict(chave="CHAVE-SECRETA", modelo="m1", reserva="m2")
            args.update(kw)
            return asyncio.run(gemini.gerar_json("sistema", "usuario", **args)), log

    def test_pedido_bem_formado(self):
        texto, log = self.rodar([ok('{"a":1}')])
        self.assertEqual(texto, '{"a":1}')
        q = log[0]
        self.assertTrue(q["url"].endswith("/models/m1:generateContent"))
        self.assertNotIn("CHAVE-SECRETA", q["url"])  # a chave só vai no cabeçalho
        self.assertEqual(q["headers"]["x-goog-api-key"], "CHAVE-SECRETA")
        self.assertEqual(q["body"]["systemInstruction"]["parts"][0]["text"], "sistema")
        self.assertEqual(q["body"]["contents"][0]["parts"][0]["text"], "usuario")
        self.assertEqual(q["body"]["generationConfig"]["responseMimeType"], "application/json")

    def test_modelo_inexistente_usa_a_reserva(self):
        texto, log = self.rodar([FakeResp(404, {}), ok("{}")])
        self.assertEqual([x["url"].split("/models/")[1] for x in log], ["m1:generateContent", "m2:generateContent"])

    def test_erro_temporario_tenta_de_novo(self):
        _, log = self.rodar([FakeResp(503, {}), ok("{}")])
        self.assertEqual(len(log), 2)

    def test_falhas_viram_iaerro(self):
        casos = {
            "sem chave": ([], dict(chave="")),
            "chave recusada": ([FakeResp(403, {})], {}),
            "conteúdo bloqueado": ([FakeResp(200, {"promptFeedback": {"blockReason": "SAFETY"}})], {}),
            "cortada": ([FakeResp(200, {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "{"}]}}]})], {}),
            "vazia": ([FakeResp(200, {"candidates": [{"finishReason": "STOP", "content": {"parts": []}}]})], {}),
            "sem json": ([FakeResp(200, None)], {}),
            "rede": ([gemini.httpx.HTTPError("x"), gemini.httpx.HTTPError("x")], {}),
        }
        for nome, (fila, kw) in casos.items():
            with self.subTest(nome), self.assertRaises(gemini.IAErro):
                self.rodar(fila, **kw)

    def test_ignora_partes_de_raciocinio(self):
        resp = FakeResp(200, {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": "pensando...", "thought": True}, {"text": '{"ok":true}'}]}}]})
        self.assertEqual(self.rodar([resp])[0], '{"ok":true}')


class TestChamarIAReal(Base):
    """Usa o chamar_ia verdadeiro (só o Gemini é simulado)."""

    def setUp(self):
        super().setUp()
        self.addCleanup(patch.stopall)
        patch.stopall()  # remove a IA falsa deste grupo
        patch.dict(os.environ, {"GEMINI_API_KEY": "CHAVE"}).start()
        self.log = []

    def com_gemini(self, fila):
        return patch.object(gemini, "novo_cliente", lambda t: FakeHTTP(fila, self.log))

    def test_feedback_completo(self):
        bom = {"resumo": "r", "observacao": "o", "sentimento": "s", "necessidade": "n", "pedido": "p",
               "exemplo": "e", "pontos_positivos": ["x"], "reflexoes": ["y"]}
        h = cadastrar("ana@x.com")
        with self.com_gemini([ok("```json\n" + json.dumps(bom) + "\n```")]):
            r = self.analisar(h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["resumo"], "r")
        self.assertEqual(len(self.log), 1)  # UMA chamada com as 4 respostas
        enviado = self.log[0]["body"]["contents"][0]["parts"][0]["text"]
        self.assertEqual(enviado.count("<situacao "), 4)
        self.assertNotIn("ana@x.com", enviado)
        self.assertNotIn("texto falso do navegador", enviado)

    def test_json_invalido_da_ia_vira_502(self):
        h = cadastrar("ana@x.com")
        with self.com_gemini([ok('{"resumo": "faltam campos"}')]):
            self.assertEqual(self.analisar(h).status_code, 502)
        self.assertEqual(A.HIST.get("ana@x.com", []), [])


if __name__ == "__main__":
    unittest.main()
