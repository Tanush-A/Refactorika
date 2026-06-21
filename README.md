# Refactorika

Refactorika is a verification harness for agent-proposed Python refactors. The
executable contract on `main` applies multi-file edits atomically and runs parse,
ruff, pyright, and pytest gates before accepting them.

Run its self-calibrating benchmark:

```bash
make benchmark
```

Run the paired harness OFF-vs-ON agent benchmark against an OpenAI-compatible
endpoint:

```bash
MODEL=qwen2.5-coder:7b make benchmark-agent
```

See [eval/README.md](eval/README.md) for methodology and output fields.
