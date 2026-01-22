# Claude Code Configuration

Este diretório contém configurações e instruções para agentes de IA que trabalham neste projeto.

## Estrutura

```
.claude/
├── CLAUDE.md           # Instruções principais para agentes de IA
├── README.md           # Este arquivo
├── credentials.md      # Credenciais do projeto (não commitado)
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

## Credenciais

O arquivo `credentials.md` contém tokens e credenciais necessárias para:

- **PyPI**: Publicação de pacotes
- **GitHub**: Criação de releases e operações via API
- **GitHub Actions**: Secrets para workflows

⚠️ **IMPORTANTE**: Este arquivo está no `.gitignore` e nunca deve ser commitado.

### Para Maintainers

Se você tem acesso de manutenção ao projeto:

1. Leia `credentials.md` para ver os tokens
2. Configure os tokens localmente conforme instruções
3. Mantenha backup seguro dos tokens
4. Nunca compartilhe os tokens publicamente

## Para Colaboradores

Se você está usando Claude Code ou outra ferramenta de IA para contribuir com este projeto:

1. Leia `CLAUDE.md` primeiro
2. Use os agentes especializados quando apropriado
3. Siga os padrões de código descritos
4. Execute os testes antes de abrir PRs

## Nota

Estes arquivos são específicos para ferramentas de IA e não afetam o funcionamento do código ou da biblioteca.
