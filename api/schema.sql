
-- Contas e consentimentos (RF01, RF02, LGPD)
CREATE TABLE IF NOT EXISTS usuarios (
    id          TEXT PRIMARY KEY,
    nome        TEXT        NOT NULL,
    email       VARCHAR(120) NOT NULL UNIQUE CHECK (email = lower(email)),
    senha_hash  TEXT        NOT NULL,                       -- scrypt com sal; a senha nunca é guardada
    perfil      VARCHAR(10) NOT NULL CHECK (perfil IN ('aluno', 'professor')),
    menor       BOOLEAN     NOT NULL DEFAULT FALSE,          -- declarou ter menos de 18 anos
    criado_em   TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS consentimentos (
    usuario_id  TEXT        NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    ordem       INTEGER     NOT NULL,
    tipo        VARCHAR(12) NOT NULL CHECK (tipo IN ('termos', 'responsavel', 'ia')),
    aceito      BOOLEAN     NOT NULL,
    versao      TEXT        NOT NULL,                       -- versão do aviso de privacidade aceita
    data        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (usuario_id, ordem)
);

-- Catálogo: temas e situações (RF03, RN02, RN03)
CREATE TABLE IF NOT EXISTS temas (
    id    TEXT PRIMARY KEY,
    nome  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cenarios (
    id         TEXT     PRIMARY KEY,
    tema_id    TEXT     NOT NULL REFERENCES temas(id) ON DELETE CASCADE,
    nivel      SMALLINT NOT NULL CHECK (nivel BETWEEN 1 AND 3),
    ordem      SMALLINT NOT NULL,
    texto      TEXT     NOT NULL,
    criterios  TEXT     NOT NULL,                           -- critérios definidos previamente para a situação
    ativo      BOOLEAN  NOT NULL DEFAULT TRUE,
    UNIQUE (tema_id, nivel, ordem)
);

-- Progresso e rascunho (RN06)
CREATE TABLE IF NOT EXISTS progresso (
    usuario_id  TEXT     NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    tema_id     TEXT     NOT NULL REFERENCES temas(id)    ON DELETE CASCADE,
    nivel       SMALLINT NOT NULL CHECK (nivel BETWEEN 1 AND 3),
    ordem       INTEGER  NOT NULL,
    PRIMARY KEY (usuario_id, tema_id, nivel)
);

CREATE TABLE IF NOT EXISTS rascunhos (
    usuario_id     TEXT PRIMARY KEY REFERENCES usuarios(id) ON DELETE CASCADE,
    tema_id        TEXT NOT NULL REFERENCES temas(id) ON DELETE CASCADE,
    indice         SMALLINT NOT NULL,
    respostas      JSONB NOT NULL,
    atualizado_em  TIMESTAMPTZ NOT NULL
);

-- Histórico do estudante (RF04 a RF07, RN06)
CREATE TABLE IF NOT EXISTS historico (
    id            TEXT PRIMARY KEY,
    usuario_id    TEXT        NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    ordem         INTEGER     NOT NULL,                     -- 0 = mais recente
    tema_id       TEXT        REFERENCES temas(id) ON DELETE SET NULL,   -- vazio nas atividades do professor
    tema_nome     TEXT        NOT NULL,
    nivel         SMALLINT    CHECK (nivel BETWEEN 1 AND 3),
    status        TEXT        NOT NULL,
    concluida_em  TIMESTAMPTZ NOT NULL,
    feedback      JSONB       NOT NULL
);

CREATE TABLE IF NOT EXISTS historico_respostas (
    historico_id  TEXT     NOT NULL REFERENCES historico(id) ON DELETE CASCADE,
    ordem         SMALLINT NOT NULL,
    cenario_id    TEXT     NOT NULL,
    situacao      TEXT     NOT NULL,                        -- texto da situação no momento da resposta
    resposta      TEXT     NOT NULL,
    PRIMARY KEY (historico_id, ordem)
);

-- Salas e atividades do professor
CREATE TABLE IF NOT EXISTS salas (
    id            TEXT PRIMARY KEY,
    nome          TEXT        NOT NULL,
    codigo        VARCHAR(12) NOT NULL UNIQUE,
    professor_id  TEXT        NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    aberta        BOOLEAN     NOT NULL DEFAULT TRUE,
    contador      INTEGER     NOT NULL DEFAULT 0,           -- último número anônimo entregue
    criada_em     TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS sala_membros (
    sala_id     TEXT        NOT NULL REFERENCES salas(id)    ON DELETE CASCADE,
    usuario_id  TEXT        NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    anon        INTEGER     NOT NULL,                       -- "Estudante N" que o professor enxerga
    entrou_em   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (sala_id, usuario_id),
    UNIQUE (sala_id, anon)
);

CREATE TABLE IF NOT EXISTS atividades_exclusivas (
    id         TEXT PRIMARY KEY,
    sala_id    TEXT        NOT NULL REFERENCES salas(id) ON DELETE CASCADE,
    ordem      INTEGER     NOT NULL,
    titulo     TEXT        NOT NULL,
    ativo      BOOLEAN     NOT NULL DEFAULT TRUE,
    criada_em  TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS cenarios_exclusivos (
    id            TEXT PRIMARY KEY,
    atividade_id  TEXT     NOT NULL REFERENCES atividades_exclusivas(id) ON DELETE CASCADE,
    ordem         SMALLINT NOT NULL,
    texto         TEXT     NOT NULL
);

CREATE TABLE IF NOT EXISTS sala_atividades (
    id          TEXT PRIMARY KEY,
    sala_id     TEXT        NOT NULL REFERENCES salas(id)    ON DELETE CASCADE,
    usuario_id  TEXT        NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    ordem       INTEGER     NOT NULL,
    anon        INTEGER     NOT NULL,
    tema_id     TEXT        REFERENCES temas(id) ON DELETE SET NULL,
    tema_nome   TEXT        NOT NULL,
    nivel       SMALLINT    CHECK (nivel BETWEEN 1 AND 3),
    feita_em    TIMESTAMPTZ NOT NULL,
    feedback    JSONB       NOT NULL
);

CREATE TABLE IF NOT EXISTS sala_respostas (
    atividade_id  TEXT     NOT NULL REFERENCES sala_atividades(id) ON DELETE CASCADE,
    ordem         SMALLINT NOT NULL,
    situacao      TEXT     NOT NULL,
    resposta      TEXT     NOT NULL,
    PRIMARY KEY (atividade_id, ordem)
);

CREATE INDEX IF NOT EXISTS idx_historico_usuario  ON historico(usuario_id, ordem);
CREATE INDEX IF NOT EXISTS idx_salas_professor    ON salas(professor_id);
CREATE INDEX IF NOT EXISTS idx_sala_atividades    ON sala_atividades(sala_id, usuario_id);
