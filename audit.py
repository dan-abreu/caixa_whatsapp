import subprocess
import sys
import os


RUFF_TARGETS = "app scripts tests audit.py"
VULTURE_TARGETS = "app scripts tests audit.py"
MYPY_TARGETS = "app scripts audit.py"
BANDIT_TARGETS = "app scripts"


def print_header(title):
    print(f"\n{'='*50}")
    print(f"🚀 {title}")
    print(f"{'='*50}\n")


def run_command(command, description):
    print_header(description)
    try:
        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        # Roda o comando e mostra a saída no terminal em tempo real
        result = subprocess.run(command, shell=True, text=True, capture_output=True, env=env)
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)

        if result.returncode == 0:
            print("✅ Verificação concluída com sucesso. Nenhum erro crítico.")
        else:
            print(f"⚠️ Atenção necessária. Código de saída: {result.returncode}")
    except Exception as e:
        print(f"❌ Falha ao executar {command}: {e}")


def main():
    # 1. Ruff: Linting ultrarrápido e formatação (substitui Flake8, Isort, etc)
    run_command(f"ruff check {RUFF_TARGETS}", "RUFF: Análise Estática e Linting")

    # 2. Vulture: Encontrar código morto (funções e variáveis não usadas)
    run_command(f"vulture {VULTURE_TARGETS} --min-confidence 80 --exclude .venv", "VULTURE: Busca por Código Morto")

    # 3. Mypy: Checagem estática de tipos (Essencial para Pydantic/FastAPI)
    # Ignora pacotes de terceiros sem tipagem definida
    run_command(f"mypy {MYPY_TARGETS} --ignore-missing-imports", "MYPY: Checagem de Tipos")

    # 4. Bandit: Análise de vulnerabilidades de segurança
    # -r para recursivo, -ll para ignorar avisos de baixo risco, -i para formato interativo/texto
    run_command(f"bandit -r {BANDIT_TARGETS} -ll -q", "BANDIT: Análise de Segurança")

    print_header("🎯 AUDITORIA ESTÁTICA FINALIZADA")
    print(
        """
📌 PRÓXIMO PASSO - PERFORMANCE (Profiling):
Para analisar a velocidade e renderização, não usamos análise estática.
Inicie seu servidor com o PyInstrument usando o comando abaixo:

    pyinstrument -m uvicorn main:app --reload

Navegue pelo sistema. Quando você parar o servidor (Ctrl+C),
ele gerará um relatório detalhado mostrando qual linha de código está atrasando o site.
"""
    )


if __name__ == "__main__":
    # Garante que está rodando no diretório do script
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()