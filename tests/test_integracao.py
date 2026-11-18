"""Fase 8: testes de integração do jogo completo.

Usam o servidor real (a classe Servidor numa thread, ou servidor.py como processo)
e dois clientes conectados por sockets TCP em 127.0.0.1, numa porta livre. Os
tabuleiros são fixos para que acertos e vitória sejam previsíveis:

    Jogador 1: navios A1-B1 e D4-D5        Jogador 2: navios D1-E1 e B3-B4

O posicionamento automático (sorteio) continua sendo o do jogo normal: só a
fábrica de tabuleiros do Servidor é trocada nos testes, como já faz
test_servidor.py. O teste com processos (TestProcessos) usa o servidor.py e o
cliente.py sem nenhuma troca, com navios sorteados.

Todo socket, leitor, thread de servidor e processo é encerrado em addCleanup,
mesmo quando o teste falha; as esperas têm prazo limitado.

Executar a partir da raiz do projeto:
    py -3 -m unittest discover -s tests -p "test_integracao.py" -v
"""

import codecs
import contextlib
import io
import itertools
import os
import socket
import struct
import subprocess
import sys
import _thread
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from cliente import ENCERRADO, MINHA_VEZ, VEZ_ADVERSARIO, conectar  # noqa: E402
from protocolo import ConexaoEncerrada, enviar_mensagem, receber_mensagem  # noqa: E402
from servidor import AGUARDANDO_SEGUNDO, SEM_JOGADORES, VEZ_J1, VEZ_J2, Servidor  # noqa: E402
from tabuleiro import Tabuleiro  # noqa: E402

NAVIOS_J1 = [("A1", "B1"), ("D4", "D5")]
NAVIOS_J2 = [("D1", "E1"), ("B3", "B4")]
VAZIO = ["~~~~~"] * 5
PRAZO = 5.0  # segundos: limite de qualquer espera


def casas_n(tabuleiro: list[str]) -> set[str]:
    """Coordenadas com navio intacto (N) num tabuleiro de 5 strings."""
    return {
        f"{'ABCDE'[coluna]}{linha + 1}"
        for linha, texto in enumerate(tabuleiro)
        for coluna, caractere in enumerate(texto)
        if caractere == "N"
    }


class ClienteRaw:
    """Cliente TCP mínimo: um socket, um leitor único e o protocolo.py."""

    def __init__(self, porta: int) -> None:
        self.sock = socket.create_connection(("127.0.0.1", porta), timeout=PRAZO)
        self.leitor = self.sock.makefile("rb")

    def enviar(self, mensagem: dict) -> None:
        enviar_mensagem(self.sock, mensagem)

    def enviar_bytes(self, dados: bytes) -> None:
        self.sock.sendall(dados)

    def receber(self) -> dict:
        return receber_mensagem(self.leitor)

    def derrubar(self) -> None:
        """Queda brusca: fecha com RST (SO_LINGER 0), sem SAIR e sem FIN educado."""
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        self.fechar()

    def fechar(self) -> None:
        for recurso in (self.leitor, self.sock):
            try:
                recurso.close()
            except OSError:
                pass


class BaseIntegracao(unittest.TestCase):
    """Sobe um servidor real com navios fixos e oferece helpers de partida."""

    def setUp(self) -> None:
        # Fábrica cíclica: o 1º tabuleiro de cada partida é o do jogador 1.
        navios = itertools.cycle([NAVIOS_J1, NAVIOS_J2])
        self.servidor = Servidor(
            "127.0.0.1", 0, criar_tabuleiro=lambda: Tabuleiro(next(navios))
        )
        _, self.porta = self.servidor.iniciar()
        self.thread = threading.Thread(target=self.servidor.servir_para_sempre, daemon=True)
        self.thread.start()
        self.clientes: list[ClienteRaw] = []
        self.addCleanup(self._encerrar)

    def _encerrar(self) -> None:
        for cliente in self.clientes:
            cliente.fechar()
        self.servidor.encerrar()
        self.thread.join(PRAZO)

    def conectar(self) -> ClienteRaw:
        cliente = ClienteRaw(self.porta)
        self.clientes.append(cliente)
        return cliente

    def esperar_estado(self, esperado: str) -> None:
        limite = time.monotonic() + PRAZO
        while time.monotonic() < limite:
            if self.servidor.estado == esperado:
                return
            time.sleep(0.01)
        self.fail(f"estado do servidor {self.servidor.estado!r}, esperado {esperado!r}")

    def iniciar_partida(self) -> tuple[ClienteRaw, ClienteRaw, dict, dict]:
        """Conecta os dois jogadores; devolve (j1, j2, INICIO de j1, INICIO de j2)."""
        j1 = self.conectar()
        self.assertEqual(j1.receber()["tipo"], "AGUARDE")
        j2 = self.conectar()
        inicio_1, inicio_2 = j1.receber(), j2.receber()
        self.assertEqual(inicio_1["tipo"], "INICIO")
        self.assertEqual(inicio_2["tipo"], "INICIO")
        self.esperar_estado(VEZ_J1)
        return j1, j2, inicio_1, inicio_2

    def atirar(self, jogadores: tuple[ClienteRaw, ClienteRaw], numero: int, coordenada: str):
        """Jogador `numero` (1 ou 2) dispara; devolve (msg do atirador, msg do outro)."""
        atirador, outro = jogadores[numero - 1], jogadores[2 - numero]
        atirador.enviar({"tipo": "DISPARAR", "coordenada": coordenada})
        return atirador.receber(), outro.receber()

    def esperar_fechamento(self, cliente: ClienteRaw) -> None:
        """O servidor fechou esta conexão: a próxima leitura é fim de fluxo."""
        with self.assertRaises(ConexaoEncerrada):
            cliente.receber()


# ---------------------------------------------------------------------------
# Início da partida
# ---------------------------------------------------------------------------


class TestInicioDaPartida(BaseIntegracao):
    def test_primeiro_jogador_aguarda_e_partida_comeca_com_o_segundo(self):
        j1 = self.conectar()
        self.assertEqual(
            j1.receber(),
            {"tipo": "AGUARDE", "mensagem": "Conectado. Aguardando o segundo jogador..."},
        )
        self.esperar_estado(AGUARDANDO_SEGUNDO)  # ainda sem partida

        j2 = self.conectar()
        self.assertEqual(j1.receber()["tipo"], "INICIO")
        self.assertEqual(j2.receber()["tipo"], "INICIO")  # o 2º nunca recebe AGUARDE
        self.esperar_estado(VEZ_J1)

    def test_ambos_recebem_suas_informacoes_e_sabem_quem_comeca(self):
        _, _, inicio_1, inicio_2 = self.iniciar_partida()
        self.assertEqual(
            inicio_1,
            {
                "tipo": "INICIO",
                "jogador": 1,
                "sua_vez": True,
                "tabuleiro_proprio": ["NN~~~", "~~~~~", "~~~~~", "~~~N~", "~~~N~"],
                "tabuleiro_adversario": VAZIO,
            },
        )
        self.assertEqual(
            inicio_2,
            {
                "tipo": "INICIO",
                "jogador": 2,
                "sua_vez": False,
                "tabuleiro_proprio": ["~~~NN", "~~~~~", "~N~~~", "~N~~~", "~~~~~"],
                "tabuleiro_adversario": VAZIO,
            },
        )

    def test_cada_jogador_ve_so_os_proprios_navios_e_nenhum_do_adversario(self):
        j1, j2, inicio_1, inicio_2 = self.iniciar_partida()
        for inicio in (inicio_1, inicio_2):
            self.assertEqual("".join(inicio["tabuleiro_proprio"]).count("N"), 4)
            self.assertNotIn("N", "".join(inicio["tabuleiro_adversario"]))
        # Os dois tabuleiros próprios são diferentes: ninguém recebeu o do outro.
        self.assertNotEqual(inicio_1["tabuleiro_proprio"], inicio_2["tabuleiro_proprio"])

    def test_privacidade_se_mantem_durante_toda_a_partida_ate_a_vitoria(self):
        jogadores = self.iniciar_partida()[:2]
        jogadas = [(1, "D1"), (2, "C1"), (1, "E1"), (2, "C2"), (1, "B3"), (2, "C3"), (1, "B4")]
        casas_n_permitidas = {1: {"A1", "B1", "D4", "D5"}, 2: {"D1", "E1", "B3", "B4"}}
        for numero, coordenada in jogadas:
            mensagens = self.atirar(jogadores, numero, coordenada)
            # atirar() devolve (mensagem do atirador, mensagem do outro).
            for dono, mensagem in zip((numero, 3 - numero), mensagens):
                self.assertEqual(mensagem["tipo"], "ATUALIZACAO")
                self.assertNotIn("N", "".join(mensagem["tabuleiro_adversario"]))
                # Os N do tabuleiro próprio só estão nas casas dos navios do próprio dono.
                self.assertLessEqual(casas_n(mensagem["tabuleiro_proprio"]), casas_n_permitidas[dono])
        # Depois da vitória o FIM não traz tabuleiros: nada novo a revelar.
        for jogador in jogadores:
            fim = jogador.receber()
            self.assertEqual(fim["tipo"], "FIM")
            self.assertNotIn("tabuleiro_adversario", fim)
            self.assertNotIn("tabuleiro_proprio", fim)


# ---------------------------------------------------------------------------
# Disparos
# ---------------------------------------------------------------------------


class TestDisparos(BaseIntegracao):
    def test_disparo_valido_atualiza_os_dois_clientes(self):
        """Exemplo da seção 10 do readme: disparo de B3 pelo jogador 1."""
        jogadores = self.iniciar_partida()[:2]
        para_j1, para_j2 = self.atirar(jogadores, 1, "B3")
        self.assertEqual(
            para_j1,
            {
                "tipo": "ATUALIZACAO",
                "atirador": 1,
                "coordenada": "B3",
                "resultado": "ACERTO",
                "sua_vez": False,
                "tabuleiro_proprio": ["NN~~~", "~~~~~", "~~~~~", "~~~N~", "~~~N~"],
                "tabuleiro_adversario": ["~~~~~", "~~~~~", "~X~~~", "~~~~~", "~~~~~"],
                "navios_restantes": {"proprio": 2, "adversario": 2},
            },
        )
        self.assertEqual(
            para_j2,
            {
                "tipo": "ATUALIZACAO",
                "atirador": 1,
                "coordenada": "B3",
                "resultado": "ACERTO",
                "sua_vez": True,
                "tabuleiro_proprio": ["~~~NN", "~~~~~", "~X~~~", "~N~~~", "~~~~~"],
                "tabuleiro_adversario": VAZIO,
                "navios_restantes": {"proprio": 2, "adversario": 2},
            },
        )
        self.esperar_estado(VEZ_J2)

    def test_a_vez_alterna_a_cada_disparo_valido(self):
        jogadores = self.iniciar_partida()[:2]
        a, b = self.atirar(jogadores, 1, "C3")  # água também passa a vez
        self.assertEqual((a["sua_vez"], b["sua_vez"]), (False, True))
        a, b = self.atirar(jogadores, 2, "C1")
        self.assertEqual((a["sua_vez"], b["sua_vez"]), (False, True))  # a = j2, b = j1
        self.esperar_estado(VEZ_J1)

    def test_agua_acerto_e_afundamento(self):
        jogadores = self.iniciar_partida()[:2]
        a, b = self.atirar(jogadores, 1, "C3")
        self.assertEqual(a["resultado"], "AGUA")
        self.assertEqual(a["tabuleiro_adversario"][2], "~~O~~")
        self.assertEqual(b["tabuleiro_proprio"][2], "~NO~~")  # B3 ainda intacto, C3 foi água

        self.atirar(jogadores, 2, "C1")
        a, b = self.atirar(jogadores, 1, "D1")
        self.assertEqual(a["resultado"], "ACERTO")
        self.assertEqual(a["navios_restantes"], {"proprio": 2, "adversario": 2})

        self.atirar(jogadores, 2, "C2")
        a, b = self.atirar(jogadores, 1, "E1")
        self.assertEqual(a["resultado"], "AFUNDOU")
        self.assertEqual(b["resultado"], "AFUNDOU")
        self.assertEqual(a["navios_restantes"], {"proprio": 2, "adversario": 1})
        self.assertEqual(b["navios_restantes"], {"proprio": 1, "adversario": 2})
        self.assertEqual(a["tabuleiro_adversario"][0], "~~~XX")

    def test_vitoria_envia_atualizacao_final_e_depois_fim_aos_dois(self):
        j1, j2, _, _ = self.iniciar_partida()
        jogadores = (j1, j2)
        for numero, coordenada in [(1, "D1"), (2, "C1"), (1, "E1"), (2, "C2"), (1, "B3"), (2, "C3")]:
            self.atirar(jogadores, numero, coordenada)

        j1.enviar({"tipo": "DISPARAR", "coordenada": "B4"})
        for jogador, navios in ((j1, {"proprio": 2, "adversario": 0}),
                                (j2, {"proprio": 0, "adversario": 2})):
            ultima = jogador.receber()
            self.assertEqual(ultima["tipo"], "ATUALIZACAO")  # primeiro a atualização...
            self.assertEqual(ultima["resultado"], "AFUNDOU")
            self.assertEqual(ultima["coordenada"], "B4")
            self.assertFalse(ultima["sua_vez"])  # sem vez para ninguém
            self.assertEqual(ultima["navios_restantes"], navios)
            fim = jogador.receber()  # ...depois o FIM
            self.assertEqual(
                fim,
                {
                    "tipo": "FIM",
                    "motivo": "VITORIA",
                    "vencedor": 1,
                    "mensagem": "Jogador 1 afundou todos os navios do adversário.",
                },
            )
            self.esperar_fechamento(jogador)  # o servidor fecha as duas conexões
        self.esperar_estado(SEM_JOGADORES)

    def test_vitoria_do_jogador_2(self):
        jogadores = self.iniciar_partida()[:2]
        j1, j2 = jogadores
        for numero, coordenada in [(1, "C3"), (2, "A1"), (1, "C4"), (2, "B1"),
                                   (1, "C5"), (2, "D4")]:
            self.atirar(jogadores, numero, coordenada)
        j1.enviar({"tipo": "DISPARAR", "coordenada": "C2"})
        j1.receber(), j2.receber()
        j2.enviar({"tipo": "DISPARAR", "coordenada": "D5"})
        self.assertEqual(j2.receber()["resultado"], "AFUNDOU")
        self.assertEqual(j1.receber()["resultado"], "AFUNDOU")
        for jogador in jogadores:
            fim = jogador.receber()
            self.assertEqual((fim["tipo"], fim["motivo"], fim["vencedor"]), ("FIM", "VITORIA", 2))
            self.esperar_fechamento(jogador)


# ---------------------------------------------------------------------------
# Erros: só para quem errou, estado preservado
# ---------------------------------------------------------------------------


class TestErros(BaseIntegracao):
    def test_coordenada_invalida_gera_erro_so_para_quem_errou_e_mantem_a_vez(self):
        j1, j2, _, _ = self.iniciar_partida()
        for invalida in ("Z9", "b3", " B3", "B10", "B", "", 42, None):
            j1.enviar({"tipo": "DISPARAR", "coordenada": invalida})
            erro = j1.receber()
            self.assertEqual((erro["tipo"], erro["codigo"]), ("ERRO", "COORDENADA_INVALIDA"), invalida)
        j1.enviar({"tipo": "DISPARAR"})  # campo ausente
        self.assertEqual(j1.receber()["codigo"], "COORDENADA_INVALIDA")
        self.esperar_estado(VEZ_J1)  # a vez continua sendo do jogador 1

        # j2 não recebeu nada: a próxima mensagem dele é o disparo válido de j1.
        para_j1, para_j2 = self.atirar((j1, j2), 1, "B3")
        self.assertEqual(para_j2["tipo"], "ATUALIZACAO")
        self.assertEqual(para_j2["tabuleiro_proprio"][2], "~X~~~")
        self.assertEqual(para_j1["tabuleiro_adversario"], ["~~~~~", "~~~~~", "~X~~~", "~~~~~", "~~~~~"])

    def test_disparo_repetido_gera_erro_e_preserva_o_estado(self):
        j1, j2, _, _ = self.iniciar_partida()
        self.atirar((j1, j2), 1, "B3")
        self.atirar((j1, j2), 2, "C1")

        j1.enviar({"tipo": "DISPARAR", "coordenada": "B3"})  # B3 já foi atingida por j1
        erro = j1.receber()
        self.assertEqual(
            erro,
            {"tipo": "ERRO", "codigo": "DISPARO_REPETIDO", "mensagem": "Você já atirou em B3."},
        )
        self.esperar_estado(VEZ_J1)

        # O tabuleiro e a vez não mudaram: B4 ainda é ACERTO->AFUNDOU do mesmo navio.
        para_j1, para_j2 = self.atirar((j1, j2), 1, "B4")
        self.assertEqual(para_j1["resultado"], "AFUNDOU")
        self.assertEqual(para_j1["navios_restantes"]["adversario"], 1)
        self.assertEqual(para_j2["tipo"], "ATUALIZACAO")

    def test_tentativa_fora_da_vez_gera_erro_so_para_quem_tentou(self):
        j1, j2, _, _ = self.iniciar_partida()
        j2.enviar({"tipo": "DISPARAR", "coordenada": "A1"})  # a vez é do jogador 1
        self.assertEqual(
            j2.receber(),
            {"tipo": "ERRO", "codigo": "FORA_DE_VEZ", "mensagem": "Não é a sua vez de jogar."},
        )
        # Fora da vez vence a coordenada inválida (ordem de verificação da seção 8).
        j2.enviar({"tipo": "DISPARAR", "coordenada": "Z9"})
        self.assertEqual(j2.receber()["codigo"], "FORA_DE_VEZ")
        self.esperar_estado(VEZ_J1)

        # j1 não recebeu nada e o disparo de j2 não foi aplicado em lugar nenhum.
        para_j1, para_j2 = self.atirar((j1, j2), 1, "C3")
        self.assertEqual(para_j1["tabuleiro_proprio"], ["NN~~~", "~~~~~", "~~~~~", "~~~N~", "~~~N~"])
        self.assertEqual(para_j2["tabuleiro_adversario"], VAZIO)

    def test_fora_da_vez_do_jogador_1_depois_de_jogar(self):
        j1, j2, _, _ = self.iniciar_partida()
        self.atirar((j1, j2), 1, "B3")
        j1.enviar({"tipo": "DISPARAR", "coordenada": "C2"})
        self.assertEqual(j1.receber()["codigo"], "FORA_DE_VEZ")
        self.esperar_estado(VEZ_J2)
        # j2 ainda joga normalmente, e C2 de j1 não foi aplicado.
        para_j2, para_j1 = self.atirar((j1, j2), 2, "C1")
        self.assertEqual(para_j1["tabuleiro_adversario"][1], "~~~~~")

    def test_mensagem_invalida_e_tipo_desconhecido_nao_mudam_nada(self):
        j1, j2, _, _ = self.iniciar_partida()
        j1.enviar_bytes(b"isto nao e json\n")
        self.assertEqual(j1.receber()["codigo"], "MENSAGEM_INVALIDA")
        j1.enviar({"tipo": "PASSAR_A_VEZ"})
        self.assertEqual(j1.receber()["codigo"], "TIPO_DESCONHECIDO")
        self.esperar_estado(VEZ_J1)
        para_j1, para_j2 = self.atirar((j1, j2), 1, "A5")  # a conexão segue utilizável
        self.assertEqual(para_j1["resultado"], "AGUA")


# ---------------------------------------------------------------------------
# Saída e desconexão
# ---------------------------------------------------------------------------


FIM_SAIU = {
    "tipo": "FIM",
    "motivo": "ADVERSARIO_SAIU",
    "vencedor": None,
    "mensagem": "O adversário saiu da partida.",
}
FIM_DESCONECTOU = {
    "tipo": "FIM",
    "motivo": "ADVERSARIO_DESCONECTOU",
    "vencedor": None,
    "mensagem": "A conexão do adversário caiu.",
}


class TestSaidaEDesconexao(BaseIntegracao):
    def test_sair_do_jogador_da_vez_avisa_o_adversario(self):
        j1, j2, _, _ = self.iniciar_partida()
        j1.enviar({"tipo": "SAIR"})
        self.assertEqual(j2.receber(), FIM_SAIU)
        self.esperar_fechamento(j2)
        self.esperar_fechamento(j1)  # sem resposta ao remetente: só o fechamento
        self.esperar_estado(SEM_JOGADORES)

    def test_sair_de_quem_aguarda_a_vez_avisa_o_adversario(self):
        j1, j2, _, _ = self.iniciar_partida()
        j2.enviar({"tipo": "SAIR"})  # a vez é do jogador 1
        self.assertEqual(j1.receber(), FIM_SAIU)
        self.esperar_fechamento(j1)
        self.esperar_estado(SEM_JOGADORES)

    def test_desconexao_inesperada_do_jogador_da_vez(self):
        j1, j2, _, _ = self.iniciar_partida()
        j1.derrubar()
        self.assertEqual(j2.receber(), FIM_DESCONECTOU)
        self.esperar_fechamento(j2)
        self.esperar_estado(SEM_JOGADORES)

    def test_desconexao_detectada_quando_quem_sai_aguarda_a_vez(self):
        """O servidor lê as duas conexões o tempo todo, não só a do jogador da vez."""
        j1, j2, _, _ = self.iniciar_partida()
        j2.derrubar()  # j2 está esperando a vez de j1
        self.assertEqual(j1.receber(), FIM_DESCONECTOU)
        self.esperar_fechamento(j1)
        self.esperar_estado(SEM_JOGADORES)

    def test_fechamento_normal_sem_sair_tambem_e_desconexao(self):
        j1, j2, _, _ = self.iniciar_partida()
        self.atirar((j1, j2), 1, "B3")
        j1.fechar()  # fecha sem enviar SAIR (agora é a vez de j2)
        self.assertEqual(j2.receber(), FIM_DESCONECTOU)

    def test_mensagem_incompleta_antes_de_cair_e_desconexao(self):
        j1, j2, _, _ = self.iniciar_partida()
        j1.enviar_bytes(b'{"tipo":"DISPAR')  # sem LF: nunca vira mensagem
        j1.fechar()
        self.assertEqual(j2.receber(), FIM_DESCONECTOU)

    def test_saida_do_primeiro_jogador_durante_a_espera_libera_a_vaga(self):
        j1 = self.conectar()
        self.assertEqual(j1.receber()["tipo"], "AGUARDE")
        self.esperar_estado(AGUARDANDO_SEGUNDO)
        j1.enviar({"tipo": "SAIR"})
        self.esperar_estado(SEM_JOGADORES)
        self.esperar_fechamento(j1)

        # A vaga foi liberada: o próximo é o jogador 1, o seguinte é o 2.
        novo_1 = self.conectar()
        self.assertEqual(novo_1.receber()["tipo"], "AGUARDE")
        novo_2 = self.conectar()
        self.assertEqual(novo_1.receber()["jogador"], 1)
        self.assertEqual(novo_2.receber()["jogador"], 2)

    def test_queda_do_primeiro_jogador_durante_a_espera_libera_a_vaga(self):
        j1 = self.conectar()
        j1.receber()
        self.esperar_estado(AGUARDANDO_SEGUNDO)
        j1.derrubar()
        self.esperar_estado(SEM_JOGADORES)

        novo_1 = self.conectar()
        self.assertEqual(novo_1.receber()["tipo"], "AGUARDE")
        novo_2 = self.conectar()
        inicio_1, inicio_2 = novo_1.receber(), novo_2.receber()
        self.assertEqual((inicio_1["jogador"], inicio_1["sua_vez"]), (1, True))
        self.assertEqual((inicio_2["jogador"], inicio_2["sua_vez"]), (2, False))

    def test_disparar_enquanto_aguarda_gera_partida_nao_iniciada(self):
        j1 = self.conectar()
        j1.receber()
        j1.enviar({"tipo": "DISPARAR", "coordenada": "B3"})
        self.assertEqual(j1.receber()["codigo"], "PARTIDA_NAO_INICIADA")
        self.esperar_estado(AGUARDANDO_SEGUNDO)


# ---------------------------------------------------------------------------
# Terceiro cliente e nova partida
# ---------------------------------------------------------------------------


class TestServidorCheioENovaPartida(BaseIntegracao):
    def test_terceiro_cliente_e_recusado_e_a_partida_nao_e_afetada(self):
        j1, j2, _, _ = self.iniciar_partida()
        terceiro = self.conectar()
        erro = terceiro.receber()
        self.assertEqual(erro["tipo"], "ERRO")
        self.assertEqual(erro["codigo"], "SERVIDOR_CHEIO")
        self.esperar_fechamento(terceiro)

        # j1 e j2 continuam jogando, sem ter recebido nada por causa do terceiro.
        para_j1, para_j2 = self.atirar((j1, j2), 1, "B3")
        self.assertEqual(para_j1["resultado"], "ACERTO")
        self.assertEqual(para_j2["tipo"], "ATUALIZACAO")
        self.esperar_estado(VEZ_J2)

    def test_servidor_aceita_nova_partida_com_estado_novo_apos_a_vitoria(self):
        j1, j2, _, _ = self.iniciar_partida()
        for numero, coordenada in [(1, "D1"), (2, "C1"), (1, "E1"), (2, "C2"),
                                   (1, "B3"), (2, "C3"), (1, "B4")]:
            self.atirar((j1, j2), numero, coordenada)
        for jogador in (j1, j2):
            self.assertEqual(jogador.receber()["tipo"], "FIM")
            self.esperar_fechamento(jogador)
        self.esperar_estado(SEM_JOGADORES)

        # Nova dupla: tudo do zero (tabuleiros sem tiros, jogador 1 começa).
        n1 = self.conectar()
        self.assertEqual(n1.receber()["tipo"], "AGUARDE")
        n2 = self.conectar()
        inicio_1, inicio_2 = n1.receber(), n2.receber()
        self.assertEqual(inicio_1["tabuleiro_proprio"], ["NN~~~", "~~~~~", "~~~~~", "~~~N~", "~~~N~"])
        self.assertEqual(inicio_2["tabuleiro_proprio"], ["~~~NN", "~~~~~", "~N~~~", "~N~~~", "~~~~~"])
        self.assertEqual(inicio_1["tabuleiro_adversario"], VAZIO)
        self.assertEqual((inicio_1["sua_vez"], inicio_2["sua_vez"]), (True, False))
        self.esperar_estado(VEZ_J1)

        # D1 foi usada na partida anterior e agora é um disparo válido de novo.
        para_n1, _ = self.atirar((n1, n2), 1, "D1")
        self.assertEqual(para_n1["resultado"], "ACERTO")
        self.assertEqual(para_n1["navios_restantes"], {"proprio": 2, "adversario": 2})

    def test_servidor_aceita_nova_partida_apos_saida_de_um_jogador(self):
        j1, j2, _, _ = self.iniciar_partida()
        self.atirar((j1, j2), 1, "B3")
        j2.enviar({"tipo": "SAIR"})
        self.assertEqual(j1.receber(), FIM_SAIU)
        self.esperar_fechamento(j1)
        self.esperar_estado(SEM_JOGADORES)

        n1 = self.conectar()
        self.assertEqual(n1.receber()["tipo"], "AGUARDE")
        n2 = self.conectar()
        self.assertEqual(n1.receber()["tabuleiro_adversario"], VAZIO)
        self.assertEqual(n2.receber()["tabuleiro_adversario"], VAZIO)
        para_n1, _ = self.atirar((n1, n2), 1, "B3")  # B3 não consta como repetido
        self.assertEqual(para_n1["resultado"], "ACERTO")


# ---------------------------------------------------------------------------
# Cliente real (cliente.py) contra o servidor real
# ---------------------------------------------------------------------------


class TestClienteReal(BaseIntegracao):
    """O Cliente do cliente.py contra o servidor, com input() controlado pelo teste."""

    def _esperar(self, condicao, descricao: str) -> None:
        limite = time.monotonic() + PRAZO
        while time.monotonic() < limite:
            if condicao():
                return
            time.sleep(0.01)
        self.fail(f"tempo esgotado: {descricao}")

    def _teclado_parado(self):
        """input() que não devolve nada até o fim do teste (ninguém digita)."""
        liberar = threading.Event()
        self.addCleanup(liberar.set)

        def teclado_bloqueado(prompt=""):
            liberar.wait(PRAZO * 2)
            raise EOFError

        return patch("builtins.input", side_effect=teclado_bloqueado)

    def test_cliente_na_vez_encerra_ao_receber_fim_sem_esperar_o_teclado(self):
        """Regressão: o cliente bloqueado em input() deve perceber o FIM do servidor."""
        cliente = conectar("127.0.0.1", self.porta)
        self.addCleanup(cliente.fechar)
        saida = io.StringIO()
        with self._teclado_parado(), contextlib.redirect_stdout(saida):
            thread = threading.Thread(target=cliente.executar, daemon=True)
            thread.start()
            adversario = self.conectar()
            adversario.receber()  # INICIO do jogador 2 (o 2º nunca recebe AGUARDE)
            self._esperar(lambda: cliente.estado == MINHA_VEZ and cliente.entrada_pendente,
                          "cliente esperando a digitação")
            adversario.derrubar()  # o adversário cai com o cliente parado no prompt
            thread.join(PRAZO)
            vivo = thread.is_alive()
        self.assertFalse(vivo, "o cliente não encerrou sozinho ao receber o FIM")
        self.assertEqual(cliente.estado, ENCERRADO)
        self.assertIn("[FIM]", saida.getvalue())
        self.assertIn("A conexão do adversário caiu.", saida.getvalue())

    def test_ctrl_c_enquanto_aguarda_envia_sair_e_libera_a_vaga(self):
        """Ctrl+C (KeyboardInterrupt na thread principal) durante o AGUARDE vira SAIR."""
        cliente = conectar("127.0.0.1", self.porta)
        self.addCleanup(cliente.fechar)
        self.esperar_estado(AGUARDANDO_SEGUNDO)
        # O mesmo que o Ctrl+C faz: o laço da thread principal recebe KeyboardInterrupt.
        timer = threading.Timer(0.5, _thread.interrupt_main)
        timer.start()
        self.addCleanup(timer.cancel)
        with contextlib.redirect_stdout(io.StringIO()) as saida:
            cliente.executar()  # retorna sozinho: executar() trata o KeyboardInterrupt
        self.assertEqual(cliente.estado, ENCERRADO)
        self.assertIn("Enviando SAIR", saida.getvalue())
        self.esperar_estado(SEM_JOGADORES)  # o servidor recebeu o SAIR: vaga liberada

    def test_cliente_recebe_atualizacoes_enquanto_aguarda_o_adversario(self):
        """Cliente real como jogador 2: fica em VEZ_ADVERSARIO e reage ao que o servidor envia."""
        raw = self.conectar()  # jogador 1
        raw.receber()  # AGUARDE
        cliente = conectar("127.0.0.1", self.porta)  # jogador 2
        self.addCleanup(cliente.fechar)
        saida = io.StringIO()
        with self._teclado_parado(), contextlib.redirect_stdout(saida):
            thread = threading.Thread(target=cliente.executar, daemon=True)
            thread.start()
            raw.receber()  # INICIO do jogador 1
            self._esperar(lambda: cliente.estado == VEZ_ADVERSARIO, "cliente em VEZ_ADVERSARIO")
            raw.enviar({"tipo": "DISPARAR", "coordenada": "B3"})
            raw.receber()  # ATUALIZACAO do jogador 1
            self._esperar(lambda: cliente.estado == MINHA_VEZ, "cliente recebeu a ATUALIZACAO")
            self.assertEqual(cliente.tabuleiro_proprio[2], "~X~~~")
            raw.enviar({"tipo": "SAIR"})  # o cliente está parado no prompt (input simulado)
            thread.join(PRAZO)
            vivo = thread.is_alive()
        self.assertFalse(vivo, "o cliente não encerrou ao receber o FIM")
        texto = saida.getvalue()
        self.assertIn("Adversário atirou em B3: ACERTO", texto)
        self.assertIn("O adversário saiu da partida.", texto)


# ---------------------------------------------------------------------------
# Processos reais: servidor.py e cliente.py pelo terminal
# ---------------------------------------------------------------------------


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Processo:
    """Um subprocesso Python com stdout/stderr coletados por uma thread (sem bloquear o teste)."""

    def __init__(self, caso: unittest.TestCase, *argumentos: str) -> None:
        ambiente = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen(
            [sys.executable, "-u", *argumentos],
            cwd=RAIZ,
            env=ambiente,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.texto = ""
        self._lock = threading.Lock()
        self._leitor = threading.Thread(target=self._coletar, daemon=True)
        self._leitor.start()
        caso.addCleanup(self.encerrar)

    def _coletar(self) -> None:
        # Lê em blocos (não por linha): o prompt do input() não termina em quebra de linha.
        decodificador = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            try:
                bloco = self.proc.stdout.read1(4096)
            except (OSError, ValueError):
                return
            if not bloco:
                return
            with self._lock:
                self.texto += decodificador.decode(bloco)

    def saida(self) -> str:
        with self._lock:
            return self.texto

    def esperar_texto(self, trecho: str, prazo: float = PRAZO) -> str:
        limite = time.monotonic() + prazo
        while time.monotonic() < limite:
            if trecho in self.saida():
                return self.saida()
            if self.proc.poll() is not None:
                time.sleep(0.1)  # dá tempo de a thread coletar o final
                break
            time.sleep(0.02)
        if trecho not in self.saida():
            raise AssertionError(f"{trecho!r} não apareceu. Saída:\n{self.saida()}")
        return self.saida()

    def escrever(self, linha: str) -> None:
        self.proc.stdin.write((linha + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def esperar_saida_do_processo(self, prazo: float = PRAZO) -> int:
        try:
            return self.proc.wait(prazo)
        except subprocess.TimeoutExpired:
            raise AssertionError(f"o processo não terminou em {prazo}s. Saída:\n{self.saida()}")

    def encerrar(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.wait(PRAZO)
        except subprocess.TimeoutExpired:
            pass
        for fluxo in (self.proc.stdin, self.proc.stdout):
            try:
                fluxo.close()
            except OSError:
                pass
        self._leitor.join(PRAZO)


class TestProcessos(unittest.TestCase):
    """servidor.py e dois cliente.py como na demonstração (tabuleiros sorteados)."""

    def setUp(self) -> None:
        self.porta = porta_livre()
        self.servidor = Processo(self, "servidor.py", str(self.porta))
        self.servidor.esperar_texto("Servidor ouvindo")

    def cliente(self) -> Processo:
        return Processo(self, "cliente.py", "127.0.0.1", str(self.porta))

    def test_partida_completa_pelo_terminal_ate_a_vitoria(self):
        c1 = self.cliente()
        c1.esperar_texto("[AGUARDE]")
        c2 = self.cliente()
        c1.esperar_texto("Você é o jogador 1")
        c2.esperar_texto("Você é o jogador 2")
        c2.esperar_texto("Aguardando o adversário atirar")

        # Entradas antecipadas no stdin: cada cliente consome uma por turno. Minúsculas
        # e espaços testam a normalização; o primeiro "ZZ" testa o erro e o novo pedido.
        c1.escrever("ZZ")
        for linha in ("a1", " A2 ", "A3", "A4", "A5", "B1", "B2", "B3", "B4", "B5",
                      "C1", "C2", "C3", "C4", "C5", "D1", "D2", "D3", "D4", "D5",
                      "E1", "E2", "E3", "E4", "E5"):
            c1.escrever(linha)
            c2.escrever(linha)

        self.assertEqual(c1.esperar_saida_do_processo(), 0)
        self.assertEqual(c2.esperar_saida_do_processo(), 0)
        s1, s2 = c1.saida(), c2.saida()
        self.assertIn("Coordenada inválida", s1)
        self.assertEqual(s1.count("[FIM]"), 1)
        self.assertEqual(s2.count("[FIM]"), 1)
        venceu_1 = "Você venceu!" in s1
        venceu_2 = "Você venceu!" in s2
        self.assertNotEqual(venceu_1, venceu_2, "exatamente um jogador deve vencer")
        self.assertIn("afundou todos os navios", s1)
        self.assertIn("afundou todos os navios", s2)

        # O servidor volta a aceitar uma nova partida depois do FIM.
        n1 = self.cliente()
        n1.esperar_texto("[AGUARDE]")
        self.assertIsNone(self.servidor.proc.poll(), "o servidor não deve encerrar com a partida")

    def test_cliente_encerra_ao_receber_fim_mesmo_esperando_digitacao(self):
        c1 = self.cliente()
        c1.esperar_texto("[AGUARDE]")
        c2 = self.cliente()
        c1.esperar_texto("Sua vez!")  # c1 parado no prompt, stdin aberto e vazio
        c2.esperar_texto("Você é o jogador 2")
        c2.proc.kill()  # queda brusca do adversário

        self.assertEqual(c1.esperar_saida_do_processo(), 0)  # sem digitar nada
        self.assertIn("[FIM] A conexão do adversário caiu.", c1.saida())

    def test_sair_digitado_encerra_o_cliente_e_avisa_o_adversario(self):
        c1 = self.cliente()
        c1.esperar_texto("[AGUARDE]")
        c2 = self.cliente()
        c1.esperar_texto("Sua vez!")
        c1.escrever("sair")
        self.assertEqual(c1.esperar_saida_do_processo(), 0)
        c2.esperar_texto("[FIM] O adversário saiu da partida.")
        self.assertEqual(c2.esperar_saida_do_processo(), 0)

    def test_terceiro_cliente_e_recusado_e_termina(self):
        c1 = self.cliente()
        c1.esperar_texto("[AGUARDE]")
        c2 = self.cliente()
        c2.esperar_texto("Você é o jogador 2")
        c3 = self.cliente()
        self.assertEqual(c3.esperar_saida_do_processo(), 0)
        self.assertIn("servidor cheio", c3.saida())

    def test_cliente_sem_servidor_informa_o_erro_e_retorna_1(self):
        sem_servidor = Processo(self, "cliente.py", "127.0.0.1", str(porta_livre()))
        self.assertEqual(sem_servidor.esperar_saida_do_processo(), 1)
        self.assertIn("Não foi possível conectar", sem_servidor.saida())


if __name__ == "__main__":
    unittest.main()
