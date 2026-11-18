"""Testes da fase 4: o cliente conecta ao servidor e recebe mensagens.

Cada teste sobe um Servidor real numa porta livre de 127.0.0.1 (igual ao
padrão de test_servidor.py) e exercita o Cliente contra ele.

Cobrem:
    - Recebimento de AGUARDE (primeiro jogador)
    - Recebimento de INICIO (cada jogador com seus campos)
    - Tratamento de SERVIDOR_CHEIO (terceiro cliente encerra o estado)
    - Envio de SAIR e encerramento limpo (da perspectiva do cliente)
    - Tratamento de ERRO PARTIDA_NAO_INICIADA sem mudar o estado
    - ConexaoEncerrada quando o servidor fecha a conexão
    - Leitura limpa de ATUALIZACAO (stubbada via servidor simulado)
    - Leitura limpa de FIM (stubbada via servidor simulado)

Executar a partir da raiz do projeto:
    python -m unittest discover -s tests -v
"""

import socket
import sys
import threading
import time
import unittest
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from protocolo import ConexaoEncerrada, enviar_mensagem, receber_mensagem  # noqa: E402
from servidor import AGUARDANDO_SEGUNDO, SEM_JOGADORES, VEZ_J1, Servidor  # noqa: E402
from tabuleiro import Tabuleiro  # noqa: E402
from cliente import (  # noqa: E402
    CONECTADO,
    ENCERRADO,
    MINHA_VEZ,
    VEZ_ADVERSARIO,
    Cliente,
    conectar,
)

# ---------------------------------------------------------------------------
# Dados de teste fixos (mesmos de test_servidor.py)
# ---------------------------------------------------------------------------

NAVIOS_J1 = [("A1", "B1"), ("D4", "D5")]
NAVIOS_J2 = [("D1", "E1"), ("B3", "B4")]
VAZIO = ["~~~~~"] * 5


# ---------------------------------------------------------------------------
# Infraestrutura compartilhada
# ---------------------------------------------------------------------------

class ClienteTeste:
    """Cliente mínimo de teste: socket raw + protocolo.py (como em test_servidor.py).

    Usado para simular o *adversário* ou para verificar o que o servidor envia,
    sem passar pela lógica da classe Cliente.
    """

    def __init__(self, porta: int) -> None:
        self.sock = socket.create_connection(("127.0.0.1", porta), timeout=5)
        self.leitor = self.sock.makefile("rb")

    def enviar(self, mensagem):
        enviar_mensagem(self.sock, mensagem)

    def receber(self):
        return receber_mensagem(self.leitor)

    def fechar(self):
        self.leitor.close()
        self.sock.close()

    def esperar_fechamento(self, caso: unittest.TestCase):
        with caso.assertRaises(ConexaoEncerrada):
            self.receber()


class BaseCliente(unittest.TestCase):
    """Sobe um Servidor real com navios fixos e oferece helpers de conexão."""

    def setUp(self):
        navios = iter([NAVIOS_J1, NAVIOS_J2] * 10)
        self.servidor = Servidor(
            "127.0.0.1", 0,
            criar_tabuleiro=lambda: Tabuleiro(next(navios)),
        )
        _, self.porta = self.servidor.iniciar()
        self.thread_servidor = threading.Thread(
            target=self.servidor.servir_para_sempre, daemon=True
        )
        self.thread_servidor.start()
        self._recursos: list = []
        self.addCleanup(self._encerrar)

    def _encerrar(self):
        for r in self._recursos:
            try:
                r.fechar()
            except Exception:
                pass
        self.servidor.encerrar()
        self.thread_servidor.join(5)

    def _registrar(self, recurso):
        self._recursos.append(recurso)
        return recurso

    def conectar_cliente(self) -> Cliente:
        """Cria um Cliente (fase 4) conectado ao servidor de teste."""
        c = conectar("127.0.0.1", self.porta)
        self._registrar(c)
        return c

    def conectar_raw(self) -> ClienteTeste:
        """Cria um cliente raw (protocolo puro) para simular o adversário."""
        c = ClienteTeste(self.porta)
        self._registrar(c)
        return c

    def esperar_estado_servidor(self, esperado: str, timeout: float = 5.0):
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            if self.servidor.estado == esperado:
                return
            time.sleep(0.01)
        self.fail(f"estado do servidor {self.servidor.estado!r}, esperado {esperado!r}")

    def _executar_cliente_em_thread(self, cliente: Cliente) -> threading.Thread:
        """Roda cliente.executar() numa thread daemon e devolve a thread."""
        t = threading.Thread(target=cliente.executar, daemon=True)
        t.start()
        return t


# ---------------------------------------------------------------------------
# Servidor simulado simples (sem regras de jogo) para testes de FIM/ATUALIZACAO
# ---------------------------------------------------------------------------

class ServidorSimulado:
    """Ouve numa porta livre, aceita UMA conexão e deixa o teste controlar o que envia."""

    def __init__(self):
        self._escuta = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._escuta.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._escuta.bind(("127.0.0.1", 0))
        self._escuta.listen(1)
        self._escuta.settimeout(5)
        self.porta = self._escuta.getsockname()[1]
        self._conn: socket.socket | None = None
        self._leitor = None

    def aceitar(self):
        conn, _ = self._escuta.accept()
        conn.setblocking(True)
        self._conn = conn
        self._leitor = conn.makefile("rb")

    def enviar(self, mensagem: dict):
        enviar_mensagem(self._conn, mensagem)

    def receber(self) -> dict:
        return receber_mensagem(self._leitor)

    def fechar(self):
        for r in [self._leitor, self._conn, self._escuta]:
            if r is not None:
                try:
                    r.close()
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Testes: recebimento de AGUARDE
# ---------------------------------------------------------------------------

class TestAguarde(BaseCliente):

    def test_cliente_recebe_aguarde_e_permanece_em_conectado(self):
        """O primeiro jogador deve receber AGUARDE e ficar no estado CONECTADO."""
        cliente = self.conectar_cliente()
        # Coleta a saída textual para não poluir o terminal de testes.
        saida = StringIO()

        # Executamos um ciclo manual: receber UMA mensagem sem o loop completo.
        # Isso evita bloquear para sempre (o loop só para com ENCERRADO).
        from protocolo import receber_mensagem as _recv
        mensagem = _recv(cliente._leitor)

        self.assertEqual(mensagem["tipo"], "AGUARDE")
        self.assertIn("mensagem", mensagem)
        # O estado ainda não mudou porque não passamos pela máquina de estados.
        self.assertEqual(cliente.estado, CONECTADO)

    def test_exibir_aguarde_via_maquina_de_estados(self):
        """Alimenta a mensagem AGUARDE pela máquina de estados e verifica o estado."""
        cliente = self.conectar_cliente()

        # Recebe a mensagem real via protocolo e despacha manualmente.
        from protocolo import receber_mensagem as _recv
        mensagem = _recv(cliente._leitor)
        cliente._despachar(mensagem)

        self.assertEqual(cliente.estado, CONECTADO)  # AGUARDE não muda o estado


# ---------------------------------------------------------------------------
# Testes: recebimento de INICIO
# ---------------------------------------------------------------------------

class TestInicio(BaseCliente):

    def _iniciar_partida_raw(self):
        """Conecta dois clientes raw e devolve (j1_raw, j2_raw) já com INICIO lido."""
        j1 = self.conectar_raw()
        j1.receber()  # AGUARDE
        j2 = self.conectar_raw()
        j1.receber()  # INICIO do j1
        j2.receber()  # INICIO do j2
        return j1, j2

    def test_jogador_1_recebe_inicio_com_sua_vez_true(self):
        """O Cliente (jogador 1) deve transitar para MINHA_VEZ ao receber INICIO."""
        cliente = self.conectar_cliente()

        # Recebe AGUARDE
        from protocolo import receber_mensagem as _recv
        aguarde = _recv(cliente._leitor)
        self.assertEqual(aguarde["tipo"], "AGUARDE")

        # Conecta o segundo jogador (raw) para disparar o INICIO
        j2 = self.conectar_raw()
        self._registrar(j2)

        # Agora o cliente real deve receber INICIO
        inicio = _recv(cliente._leitor)
        cliente._despachar(inicio)

        self.assertEqual(cliente.estado, MINHA_VEZ)
        self.assertEqual(cliente.jogador, 1)
        self.assertIsNotNone(cliente.tabuleiro_proprio)
        self.assertIsNotNone(cliente.tabuleiro_adversario)
        self.assertEqual(cliente.tabuleiro_adversario, VAZIO)
        # Tabuleiro próprio deve ter 4 casas 'N' (2 navios × 2 casas)
        self.assertEqual("".join(cliente.tabuleiro_proprio).count("N"), 4)

        j2.receber()  # consome INICIO do j2

    def test_jogador_2_recebe_inicio_com_sua_vez_false(self):
        """O Cliente (jogador 2) deve transitar para VEZ_ADVERSARIO ao receber INICIO."""
        j1_raw = self.conectar_raw()
        j1_raw.receber()  # AGUARDE

        cliente = self.conectar_cliente()

        # Lê o INICIO do j1_raw (para liberar a mensagem)
        j1_raw.receber()  # INICIO do jogador 1

        # Cliente 2 deve receber INICIO com sua_vez=false
        from protocolo import receber_mensagem as _recv
        inicio = _recv(cliente._leitor)
        cliente._despachar(inicio)

        self.assertEqual(cliente.estado, VEZ_ADVERSARIO)
        self.assertEqual(cliente.jogador, 2)
        self.assertEqual(cliente.tabuleiro_adversario, VAZIO)

    def test_tabuleiro_proprio_contem_navios(self):
        """Após INICIO, tabuleiro_proprio deve ter exatamente 4 'N'."""
        cliente = self.conectar_cliente()

        from protocolo import receber_mensagem as _recv
        _recv(cliente._leitor)  # AGUARDE

        j2_raw = self.conectar_raw()
        self._registrar(j2_raw)

        inicio = _recv(cliente._leitor)
        cliente._despachar(inicio)

        total_n = "".join(cliente.tabuleiro_proprio).count("N")
        self.assertEqual(total_n, 4)

        j2_raw.receber()  # consome INICIO do j2


# ---------------------------------------------------------------------------
# Testes: SERVIDOR_CHEIO
# ---------------------------------------------------------------------------

class TestServidorCheio(BaseCliente):

    def test_terceiro_cliente_recebe_servidor_cheio_e_encerra(self):
        """O terceiro cliente deve receber ERRO SERVIDOR_CHEIO e transitar para ENCERRADO."""
        # Inicia a partida com dois clientes raw
        j1 = self.conectar_raw()
        j1.receber()  # AGUARDE
        j2 = self.conectar_raw()
        j1.receber()  # INICIO
        j2.receber()  # INICIO
        self.esperar_estado_servidor(VEZ_J1)

        # Terceiro cliente: usa a classe Cliente
        terceiro = self.conectar_cliente()

        from protocolo import receber_mensagem as _recv
        erro = _recv(terceiro._leitor)
        terceiro._despachar(erro)

        self.assertEqual(erro["tipo"], "ERRO")
        self.assertEqual(erro["codigo"], "SERVIDOR_CHEIO")
        self.assertEqual(terceiro.estado, ENCERRADO)

    def test_terceiro_cliente_via_loop_completo(self):
        """O loop completo do terceiro cliente deve encerrar ao receber SERVIDOR_CHEIO."""
        j1 = self.conectar_raw()
        j1.receber()
        j2 = self.conectar_raw()
        j1.receber()
        j2.receber()
        self.esperar_estado_servidor(VEZ_J1)

        terceiro = self.conectar_cliente()
        # Bloqueia ao_minha_vez para nunca enviar nada (não aplicável aqui, mas seguro)
        terceiro.ao_minha_vez = lambda: True

        t = self._executar_cliente_em_thread(terceiro)
        t.join(timeout=5)

        self.assertFalse(t.is_alive(), "O loop do cliente não encerrou após SERVIDOR_CHEIO")
        self.assertEqual(terceiro.estado, ENCERRADO)


# ---------------------------------------------------------------------------
# Testes: envio de SAIR e encerramento limpo
# ---------------------------------------------------------------------------

class TestSair(BaseCliente):

    def test_enviar_sair_enquanto_aguarda_encerra_cliente(self):
        """O cliente envia SAIR antes de o segundo jogador entrar e encerra limpamente."""
        cliente = self.conectar_cliente()

        from protocolo import receber_mensagem as _recv
        _recv(cliente._leitor)  # AGUARDE

        # Envia SAIR diretamente (sem passar pelo loop)
        cliente.enviar_sair()

        self.assertEqual(cliente.estado, ENCERRADO)
        self.esperar_estado_servidor(SEM_JOGADORES)

    def test_enviar_sair_durante_partida_avisa_adversario(self):
        """Após enviar SAIR, o adversário (raw) deve receber FIM ADVERSARIO_SAIU."""
        # Conecta jogador 1 via Cliente real
        cliente = self.conectar_cliente()
        from protocolo import receber_mensagem as _recv
        _recv(cliente._leitor)  # AGUARDE

        # Conecta jogador 2 raw
        j2_raw = self.conectar_raw()
        self._registrar(j2_raw)

        # Aguarda INICIO do j1 e j2
        _recv(cliente._leitor)   # INICIO para o cliente (jogador 1)
        j2_raw.receber()         # INICIO para o raw (jogador 2)
        self.esperar_estado_servidor(VEZ_J1)

        # Jogador 1 envia SAIR
        cliente.enviar_sair()

        # Adversário deve receber FIM
        fim = j2_raw.receber()
        self.assertEqual(fim["tipo"], "FIM")
        self.assertEqual(fim["motivo"], "ADVERSARIO_SAIU")
        self.assertIsNone(fim["vencedor"])

    def test_fechar_socket_limpa_leitor_e_socket(self):
        """_fechar() não deve levantar exceção mesmo se chamado duas vezes."""
        cliente = self.conectar_cliente()
        cliente._fechar()
        cliente._fechar()  # idempotente — sem exceção


# ---------------------------------------------------------------------------
# Testes: ERRO PARTIDA_NAO_INICIADA não muda o estado
# ---------------------------------------------------------------------------

class TestErroDuranteEspera(BaseCliente):

    def test_partida_nao_iniciada_nao_muda_estado(self):
        """ERRO PARTIDA_NAO_INICIADA não deve alterar o estado CONECTADO do cliente."""
        cliente = self.conectar_cliente()

        from protocolo import receber_mensagem as _recv
        _recv(cliente._leitor)  # AGUARDE

        # Envia DISPARAR antes de o segundo jogador conectar
        from protocolo import enviar_mensagem as _send
        _send(cliente._sock, {"tipo": "DISPARAR", "coordenada": "B3"})

        erro = _recv(cliente._leitor)
        cliente._despachar(erro)

        self.assertEqual(erro["tipo"], "ERRO")
        self.assertEqual(erro["codigo"], "PARTIDA_NAO_INICIADA")
        self.assertEqual(cliente.estado, CONECTADO)  # estado NÃO muda


# ---------------------------------------------------------------------------
# Testes: ConexaoEncerrada quando o servidor fecha a conexão
# ---------------------------------------------------------------------------

class TestConexaoPerdida(BaseCliente):

    def test_queda_do_servidor_via_loop_completo(self):
        """Se o servidor fechar a conexão, o loop deve encerrar limpamente."""
        cliente = self.conectar_cliente()
        # Sobrescreve ao_minha_vez para nunca bloquear
        cliente.ao_minha_vez = lambda: True

        # Lança o loop em background
        t = self._executar_cliente_em_thread(cliente)

        # Encerra o servidor bruscamente para derrubar a conexão
        self.servidor.encerrar()
        t.join(timeout=5)

        self.assertFalse(t.is_alive(), "O loop do cliente não encerrou após queda do servidor")
        self.assertEqual(cliente.estado, ENCERRADO)


# ---------------------------------------------------------------------------
# Testes: FIM via servidor simulado (sem regras de jogo)
# ---------------------------------------------------------------------------

class TestFim(unittest.TestCase):
    """Usa um ServidorSimulado para controlar exatamente o que é enviado ao cliente."""

    def setUp(self):
        self.sim = ServidorSimulado()
        self.addCleanup(self.sim.fechar)

    def _aceitar_em_thread(self):
        t = threading.Thread(target=self.sim.aceitar, daemon=True)
        t.start()
        return t

    def _criar_cliente_conectado(self) -> Cliente:
        t = self._aceitar_em_thread()
        c = conectar("127.0.0.1", self.sim.porta)
        t.join(5)
        return c

    def test_fim_vitoria_propria_encerra_estado(self):
        cliente = self._criar_cliente_conectado()
        cliente.jogador = 1

        self.sim.enviar({
            "tipo": "FIM",
            "motivo": "VITORIA",
            "vencedor": 1,
            "mensagem": "Jogador 1 venceu.",
        })

        from protocolo import receber_mensagem as _recv
        mensagem = _recv(cliente._leitor)
        cliente._despachar(mensagem)

        self.assertEqual(cliente.estado, ENCERRADO)
        cliente._fechar()

    def test_fim_adversario_saiu_encerra_estado(self):
        cliente = self._criar_cliente_conectado()
        cliente.jogador = 2

        self.sim.enviar({
            "tipo": "FIM",
            "motivo": "ADVERSARIO_SAIU",
            "vencedor": None,
            "mensagem": "O adversário saiu.",
        })

        from protocolo import receber_mensagem as _recv
        mensagem = _recv(cliente._leitor)
        cliente._despachar(mensagem)

        self.assertEqual(cliente.estado, ENCERRADO)
        cliente._fechar()

    def test_fim_adversario_desconectou_encerra_estado(self):
        cliente = self._criar_cliente_conectado()
        cliente.jogador = 1

        self.sim.enviar({
            "tipo": "FIM",
            "motivo": "ADVERSARIO_DESCONECTOU",
            "vencedor": None,
            "mensagem": "Conexão caiu.",
        })

        from protocolo import receber_mensagem as _recv
        mensagem = _recv(cliente._leitor)
        cliente._despachar(mensagem)

        self.assertEqual(cliente.estado, ENCERRADO)
        cliente._fechar()

    def test_loop_completo_encerra_apos_fim(self):
        """O loop executar() deve terminar sozinho quando receber FIM."""
        cliente = self._criar_cliente_conectado()
        cliente.jogador = 1
        cliente.ao_minha_vez = lambda: True  # não bloqueia se chegar MINHA_VEZ

        t = threading.Thread(target=cliente.executar, daemon=True)
        t.start()

        # Dá um instante para o cliente iniciar o loop
        time.sleep(0.05)

        self.sim.enviar({
            "tipo": "FIM",
            "motivo": "ADVERSARIO_SAIU",
            "vencedor": None,
            "mensagem": "O adversário saiu.",
        })

        t.join(timeout=5)
        self.assertFalse(t.is_alive(), "O loop não encerrou após receber FIM")
        self.assertEqual(cliente.estado, ENCERRADO)


# ---------------------------------------------------------------------------
# Testes: ATUALIZACAO via servidor simulado
# ---------------------------------------------------------------------------

class TestAtualizacao(unittest.TestCase):
    """Verifica que ATUALIZACAO atualiza tabuleiros e transita o estado corretamente."""

    def setUp(self):
        self.sim = ServidorSimulado()
        self.addCleanup(self.sim.fechar)

    def _aceitar_em_thread(self):
        t = threading.Thread(target=self.sim.aceitar, daemon=True)
        t.start()
        return t

    def _criar_cliente(self) -> Cliente:
        t = self._aceitar_em_thread()
        c = conectar("127.0.0.1", self.sim.porta)
        t.join(5)
        c.jogador = 1
        return c

    def test_atualizacao_minha_vez_true_transita_para_minha_vez(self):
        cliente = self._criar_cliente()
        self.sim.enviar({
            "tipo": "ATUALIZACAO",
            "atirador": 2,
            "coordenada": "B3",
            "resultado": "AGUA",
            "sua_vez": True,
            "tabuleiro_proprio": ["NN~~~", "~~~~~", "~~~~~", "~~~N~", "~~~N~"],
            "tabuleiro_adversario": VAZIO,
            "navios_restantes": {"proprio": 2, "adversario": 2},
        })

        from protocolo import receber_mensagem as _recv
        m = _recv(cliente._leitor)
        cliente._despachar(m)

        self.assertEqual(cliente.estado, MINHA_VEZ)
        self.assertEqual(cliente.tabuleiro_proprio, ["NN~~~", "~~~~~", "~~~~~", "~~~N~", "~~~N~"])
        cliente._fechar()

    def test_atualizacao_minha_vez_false_transita_para_vez_adversario(self):
        cliente = self._criar_cliente()
        self.sim.enviar({
            "tipo": "ATUALIZACAO",
            "atirador": 1,
            "coordenada": "A1",
            "resultado": "ACERTO",
            "sua_vez": False,
            "tabuleiro_proprio": ["NN~~~", "~~~~~", "~~~~~", "~~~N~", "~~~N~"],
            "tabuleiro_adversario": ["X~~~~", "~~~~~", "~~~~~", "~~~~~", "~~~~~"],
            "navios_restantes": {"proprio": 2, "adversario": 2},
        })

        from protocolo import receber_mensagem as _recv
        m = _recv(cliente._leitor)
        cliente._despachar(m)

        self.assertEqual(cliente.estado, VEZ_ADVERSARIO)
        cliente._fechar()


# ---------------------------------------------------------------------------
# Testes: argumentos de linha de comando (main)
# ---------------------------------------------------------------------------

class TestMain(unittest.TestCase):

    def test_main_argumentos_excessivos_retorna_2(self):
        from cliente import main
        self.assertEqual(main(["127.0.0.1", "5050", "extra"]), 2)

    def test_main_porta_invalida_retorna_2(self):
        from cliente import main
        self.assertEqual(main(["127.0.0.1", "naoéporta"]), 2)

    def test_main_porta_fora_do_range_retorna_2(self):
        from cliente import main
        self.assertEqual(main(["127.0.0.1", "0"]), 2)

    def test_main_servidor_indisponivel_retorna_1(self):
        from cliente import main
        # Porta 1 é privilegiada e normalmente recusada (ou porta muito improvável)
        # Usamos uma porta livre conhecida que ninguém está escutando.
        import socket as _s
        s = _s.socket()
        s.bind(("127.0.0.1", 0))
        porta_livre = s.getsockname()[1]
        s.close()
        self.assertEqual(main(["127.0.0.1", str(porta_livre)]), 1)


if __name__ == "__main__":
    unittest.main()
