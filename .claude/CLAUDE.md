# Instruções para Agentes de IA - rate-sync

## Tipo de Projeto

**Biblioteca Python open-source** - Rate limiter distribuído zero-config.

Publicado como **rate-sync** no PyPI. Suporta múltiplos backends: Redis, NATS, PostgreSQL, memória.

## Links Importantes

- **PyPI**: https://pypi.org/project/rate-sync/
- **GitHub**: https://github.com/zyc/rate-sync
- **Issues**: https://github.com/zyc/rate-sync/issues

## Gerenciamento de Dependências Python

**SEMPRE use Poetry. NUNCA use pip, uv, pipenv, conda.**

```bash
poetry install          # Instalar dependências
poetry add <pacote>     # Adicionar dependência
poetry run <comando>    # Executar no ambiente virtual
poetry run pytest       # Executar testes
```

## Estrutura do Projeto

```
rate-sync/
├── src/ratesync/           # Código fonte
│   ├── engines/            # Implementações de backends
│   ├── contrib/            # Integrações (FastAPI, Prometheus)
│   └── domain/             # Value objects e domain layer
├── tests/                  # Testes
│   ├── unit/              # Testes unitários
│   ├── integration/       # Testes de integração (requerem infra)
│   └── manual/            # Testes manuais
├── docs/                   # Documentação
└── examples/              # Exemplos de uso
```

## Padrões de Código

### Princípios Obrigatórios

- **Clean Architecture**: domain/ (entidades, repositórios ABC) -> infrastructure/ (implementações)
- **SOLID**: Injeção de dependência explícita, sem defaults em construtores
- **KISS**: Mínimo necessário para a tarefa atual
- **DRY**: Evite duplicação, mas não abstraia prematuramente

### Convenções Python

- Type hints obrigatórios (usar `T | None`, não `Optional[T]`)
- Docstrings Google Style
- snake_case (funções), PascalCase (classes)
- Imports: `from __future__ import annotations` no topo

### Organização de `__init__.py` e Imports

**Regra fundamental**: Barrel exports apenas nas pastas arquiteturais intermediárias.

| Nível | Exemplo | `__init__.py` |
|-------|---------|---------------|
| Base arquitetural | `domain/`, `engines/` | **NÃO** |
| Intermediário | `domain/value_objects/`, `engines/` | **SIM** (barrel exports) |
| Feature | `engines/redis/` | **NÃO** |

**Estrutura de pastas**:
```
engines/
├── __init__.py              # SIM - barrel exports de TODOS os engines
├── redis.py
├── nats.py
├── postgres.py
└── memory.py
```

**O `__init__.py` intermediário faz re-export de TODOS os engines**:
```python
# engines/__init__.py
from engines.redis import RedisEngine
from engines.memory import MemoryEngine
from engines.nats import NATSEngine
from engines.postgres import PostgresEngine

__all__ = ["RedisEngine", "MemoryEngine", "NATSEngine", "PostgresEngine"]
```

**Imports SEMPRE via barrel export intermediário**:
```python
# CORRETO - usa barrel export
from ratesync.engines import RedisEngine, MemoryEngine

# ERRADO - import direto do arquivo
from ratesync.engines.redis import RedisEngine
```

**Benefícios**:
- Imports curtos e limpos
- Encapsulamento da estrutura interna
- Facilita refatoração (mover arquivos não quebra imports externos)

**Exceção**: Import direto do arquivo é permitido APENAS para evitar referência circular.

## Testes

### Marcadores de Teste

```python
@pytest.mark.redis          # Requer Redis
@pytest.mark.nats           # Requer NATS
@pytest.mark.postgres       # Requer PostgreSQL
@pytest.mark.integration    # Requer infraestrutura externa
@pytest.mark.slow           # Teste demorado
```

### Executar Testes

```bash
# Todos os testes (exceto integração)
poetry run pytest -m "not integration"

# Apenas testes unitários rápidos
poetry run pytest -m "not integration and not slow"

# Testes de um engine específico (requer infraestrutura)
export REDIS_URL="redis://localhost:6379/0"
poetry run pytest -m redis

# Com coverage
poetry run pytest --cov=ratesync --cov-report=term
```

## CI/CD

### GitHub Actions

O projeto usa GitHub Actions para CI/CD:

- **test.yml**: Executa testes em Python 3.12 e 3.13
- **publish.yml**: Publica no PyPI em releases

### Fluxo de Release

1. Atualizar versão em `pyproject.toml`
2. Atualizar `CHANGELOG.md`
3. Commit: `git commit -m "chore: bump version to X.Y.Z"`
4. Tag: `git tag -a vX.Y.Z -m "Release X.Y.Z"`
5. Push: `git push && git push --tags`
6. Criar release no GitHub (dispara publicação no PyPI)

## Desenvolvimento

### Setup Inicial

```bash
git clone https://github.com/zyc/rate-sync.git
cd rate-sync
poetry install
poetry run pytest
```

### Code Quality

```bash
# Formatação
poetry run black src/ratesync tests

# Linting
poetry run ruff check src/ratesync

# Type checking (se mypy estiver configurado)
poetry run mypy src/ratesync
```

### Infraestrutura Local para Testes

**Redis:**
```bash
docker run -d -p 6379:6379 redis:alpine
```

**NATS:**
```bash
docker run -d -p 4222:4222 nats:alpine -js
```

**PostgreSQL:**
```bash
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=test postgres:alpine
```

## Contribuindo

### Antes de Abrir PR

1. ✅ Testes passando (`poetry run pytest -m "not integration"`)
2. ✅ Código formatado (`poetry run black .`)
3. ✅ Linting limpo (`poetry run ruff check .`)
4. ✅ Type hints completos
5. ✅ Docstrings atualizadas
6. ✅ CHANGELOG.md atualizado (se aplicável)

### Commit Messages

Seguir [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add support for Redis Cluster
fix: correct token bucket calculation
docs: update FastAPI integration guide
test: add tests for NATS engine
chore: bump dependencies
```

## Arquitetura

### Engines (Backends)

Cada engine implementa a interface `BaseEngine`:

- `MemoryEngine`: Backend em memória (single-process)
- `RedisEngine`: Backend Redis (distribuído)
- `NATSEngine`: Backend NATS JetStream (distribuído)
- `PostgresEngine`: Backend PostgreSQL (distribuído)

### Algoritmos

- **Token Bucket**: Controla throughput (rate_per_second)
- **Sliding Window**: Conta requests em janela de tempo (limit + window_seconds)

### FastAPI Integration

Disponível em `ratesync.contrib.fastapi`:

- `RateLimitDependency`: Dependency para rotas
- `RateLimitMiddleware`: Middleware global
- `rate_limit_exception_handler`: Handler de exceções

## Troubleshooting

### Poetry - Ambiente Virtual Compartilhado

**Sintoma:** Alterações no código não refletidas em runtime.

**Causa:** `VIRTUAL_ENV` definida de outro ambiente.

**Solução:**
```bash
unset VIRTUAL_ENV
rm -rf .venv
poetry install
```

### Testes de Integração Falhando

**Sintoma:** Testes marcados com `@pytest.mark.integration` falham com erro de conexão.

**Causa:** Infraestrutura (Redis/NATS/PostgreSQL) não está rodando.

**Solução:** Use `-m "not integration"` para pular esses testes, ou suba a infraestrutura necessária.

## Política de Versioning

Seguimos [Semantic Versioning](https://semver.org/):

- **MAJOR**: Mudanças incompatíveis na API
- **MINOR**: Nova funcionalidade compatível
- **PATCH**: Bug fixes compatíveis

## Licença

MIT License - veja LICENSE para detalhes.
