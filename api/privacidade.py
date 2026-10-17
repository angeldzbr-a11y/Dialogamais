"""Proteção de dados (LGPD): textos versionados, minimização e retenção."""
import os
import re

VERSAO = "2026-10-16"  # mude quando alterar os termos ou o aviso: pede novo aceite no futuro
RETENCAO_DIAS = int(os.getenv("RETENCAO_DIAS", "180"))          # histórico e atividades em salas
RETENCAO_RASCUNHO_DIAS = int(os.getenv("RETENCAO_RASCUNHO_DIAS", "14"))
CONTROLADOR = os.getenv("CONTROLADOR", "Equipe do projeto Dialoga+ (TCC FAMETRO)")
CONTATO = os.getenv("CONTATO_PRIVACIDADE", "")
PLANO_PAGO = os.getenv("GEMINI_PLANO_PAGO", "0") == "1"

_PADROES = [  # a ordem importa: o mais específico primeiro
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),                       # e-mail
    re.compile(r"https?://\S+|www\.\S+"),                              # endereço de site
    re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),                   # CPF
    re.compile(r"(?<!\d)(?:\+?55\s?)?\(?\d{2}\)?[\s.-]?9?\d{4}[\s.-]?\d{4}(?!\d)"),  # telefone
    re.compile(r"\b\d{5}-?\d{3}\b"),                                   # CEP
    re.compile(r"(?<!\w)@\w{2,}"),                                     # @usuario
]


def anonimizar(texto):
    """Troca e-mails, telefones, CPF, CEP, links e @usuários por [dado removido] antes de enviar à IA.
    Não consegue achar nomes próprios: por isso o aviso na tela pede para não escrevê-los."""
    n = 0
    for p in _PADROES:
        texto, k = p.subn("[dado removido]", texto)
        n += k
    return texto, n


def info_publica(modelo, pago):
    return {
        "versao": VERSAO, "controlador": CONTROLADOR, "contato": CONTATO,
        "provedor_ia": "Google (Gemini API)", "modelo": modelo,
        "plano_pago": pago, "modo_demonstracao": not pago,
        "retencao_dias": RETENCAO_DIAS, "retencao_rascunho_dias": RETENCAO_RASCUNHO_DIAS,
    }
