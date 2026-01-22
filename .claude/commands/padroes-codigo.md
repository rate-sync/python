# Padroes de Codigo

Revise o codigo seguindo os padroes do projeto.

## Clean Architecture

- **domain/**: Entidades, Value Objects, Repositorios (ABC), Services
- **infrastructure/**: Implementacoes Redis, NATS, PostgreSQL, Memory backends

Dependencias fluem de fora para dentro. Domain NAO depende de infrastructure.

## SOLID

- **S** (Single Responsibility): Uma classe, uma responsabilidade
- **O** (Open/Closed): Aberto para extensao, fechado para modificacao
- **L** (Liskov): Subtipos substituiveis por seus tipos base
- **I** (Interface Segregation): Interfaces pequenas e especificas
- **D** (Dependency Inversion): Depender de abstracoes (ABC), nao de implementacoes

## KISS

- Solucao mais simples que funciona
- Sem over-engineering ou features "para o futuro"
- Sem abstracoes para uso unico

## DRY

- Codigo reutilizavel vai para shared-*
- Evitar duplicacao entre projetos
- Extrair quando houver 3+ usos similares

## Convencoes Python

- Type hints obrigatorios (`T | None`, nao `Optional[T]`)
- Docstrings Google Style
- snake_case (funcoes), PascalCase (classes)
- `from __future__ import annotations` no topo
- Sem defaults em construtores (DI explicita)

## Logging

- Usar `LoggerGateway` de `shared_core.domain.gateways`
- NUNCA usar `print()` ou `logging` direto
- Injetar `_logger` via construtor (prefixo `_` indica dependencia tecnica)
- Usar contexto estruturado: `self._logger.info("msg", contexto={...})`
- Testes: usar `NullLoggerGateway`

## Referencia

Documentacao completa em `shared-core/docs/architecture/`
