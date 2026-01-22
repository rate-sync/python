# Claude Code Configuration

Este diretório contém configurações e instruções para agentes de IA que trabalham neste projeto.

## Estrutura

```
.claude/
├── CLAUDE.md           # Instruções principais para agentes de IA
├── agents/             # Agentes especializados
│   ├── code-reviewer.md
│   ├── python-developer.md
│   ├── test-engineer.md
│   └── troubleshooter.md
└── commands/           # Comandos customizados
    └── padroes-codigo.md
```

## CLAUDE.md

Documento principal com:
- Visão geral do projeto
- Padrões de código
- Estrutura do projeto
- Guia de desenvolvimento
- CI/CD
- Troubleshooting

## Agentes

Agentes especializados que podem ser invocados para tarefas específicas:

- **code-reviewer**: Revisão de código e qualidade
- **python-developer**: Desenvolvimento Python
- **test-engineer**: Testes e cobertura
- **troubleshooter**: Debug e resolução de problemas

## Comandos

Comandos customizados para tarefas comuns:

- **padroes-codigo**: Aplicar padrões de código do projeto

## Para Colaboradores

Se você está usando Claude Code ou outra ferramenta de IA para contribuir com este projeto:

1. Leia `CLAUDE.md` primeiro
2. Use os agentes especializados quando apropriado
3. Siga os padrões de código descritos
4. Execute os testes antes de abrir PRs

## Nota

Estes arquivos são específicos para ferramentas de IA e não afetam o funcionamento do código ou da biblioteca.
