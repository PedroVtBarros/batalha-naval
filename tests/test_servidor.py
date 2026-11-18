"""Testes das fases 3 e 6: o servidor recebe os jogadores e conduz a partida.

Cada teste sobe um Servidor real numa porta livre de 127.0.0.1 e conecta
clientes TCP de verdade, usando o protocolo.py. Cobrem todas as linhas (1 a 13)
da tabela 9.2 do readme.md.

Executar a partir da raiz do projeto:
    python -m unittest discover -s tests -p 'test_servidor.py' -v
"""

import socket
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from protocolo import ConexaoEncerrada, enviar_mensagem, receber_mensagem  # noqa: E402
from servidor import (  # noqa: E402
    AGUARDANDO_SEGUNDO,
    SEM_JOGADORES,
    VEZ_J1,
    VEZ_J2,
    Servidor,
)
from tabuleiro import Tabuleiro  # noqa: E402

NAVIOS_J1 = [("A1", "B1"), ("D4", "D5")]
NAVIOS_J2 = [("D1", "E1"), ("B3", "B4")]
VAZIO = ["~~~~~"] * 5


class Cliente:
    """Cliente mínimo de teste: um socket, um leitor e timeout de segurança."""

    def __init__(self, porta: int) -> None:
        self.sock = socket.create_connection(("127.0.0.1", porta), timeout=5)
        # Timeout só como rede de segurança: um erro de lógica vira falha, não trava.
        self.leitor = self.sock.makefile("rb")

    def enviar(self, mensagem):
        enviar_mensagem(self.sock, mensagem)

    def receber(self):
        return receber_mensagem(self.leitor)

    def fechar(self):
        self.leitor.close()
        self.sock.close()

    def esperar_fechamento(self, caso: unittest.TestCase):
        """Confere que o servidor fechou esta conexão."""
        with caso.assertRaises(ConexaoEncerrada):
            self.receber()


class BaseServidor(unittest.TestCase):
    def setUp(self):
        # Navios fixos e alternados: o 1º tabuleiro de cada partida é o do jogador 1.
        navios = iter([NAVIOS_J1, NAVIOS_J2] * 10)
        self.servidor = Servidor("127.0.0.1", 0, criar_tabuleiro=lambda: Tabuleiro(next(navios)))
        _, self.porta = self.servidor.iniciar()
        self.thread = threading.Thread(target=self.servidor.servir_para_sempre, daemon=True)
        self.thread.start()
        self.clientes = []
        self.addCleanup(self._encerrar)

    def _encerrar(self):
        for cliente in self.clientes:
            cliente.fechar()
        self.servidor.encerrar()
        self.thread.join(5)

    def conectar(self) -> Cliente:
        cliente = Cliente(self.porta)
        self.clientes.append(cliente)
        return cliente

    def esperar_estado(self, esperado: str):
        limite = time.monotonic() + 5
        while time.monotonic() < limite:
            if self.servidor.estado == esperado:
                return
            time.sleep(0.01)
        self.fail(f"estado {self.servidor.estado}, esperado {esperado}")

    def iniciar_partida(self):
        j1 = self.conectar()
        self.assertEqual(j1.receber()["tipo"], "AGUARDE")
        j2 = self.conectar()
        self.assertEqual(j1.receber()["tipo"], "INICIO")
        self.assertEqual(j2.receber()["tipo"], "INICIO")
        return j1, j2


class TestEntradaDosJogadores(BaseServidor):
    def test_primeiro_jogador_recebe_aguarde(self):
        j1 = self.conectar()
        self.assertEqual(
            j1.receber(),
            {"tipo": "AGUARDE", "mensagem": "Conectado. Aguardando o segundo jogador..."},
        )
        self.esperar_estado(AGUARDANDO_SEGUNDO)

    def test_segundo_jogador_inicia_a_partida_com_inicio_para_cada_um(self):
        j1 = self.conectar()
        j1.receber()
        j2 = self.conectar()
        self.assertEqual(
            j1.receber(),
            {
                "tipo": "INICIO",
                "jogador": 1,
                "sua_vez": True,
                "tabuleiro_proprio": ["NN~~~", "~~~~~", "~~~~~", "~~~N~", "~~~N~"],
                "tabuleiro_adversario": VAZIO,
            },
        )
        self.assertEqual(
            j2.receber(),
            {
                "tipo": "INICIO",
                "jogador": 2,
                "sua_vez": False,
                "tabuleiro_proprio": ["~~~NN", "~~~~~", "~N~~~", "~N~~~", "~~~~~"],
                "tabuleiro_adversario": VAZIO,
            },
        )
        self.esperar_estado(VEZ_J1)

    def test_tabuleiros_sorteados_por_padrao(self):
        self.servidor.encerrar()
        self.thread.join(5)
        self.servidor = Servidor("127.0.0.1", 0)
        _, self.porta = self.servidor.iniciar()
        self.thread = threading.Thread(target=self.servidor.servir_para_sempre, daemon=True)
        self.thread.start()
        j1 = self.conectar()
        j1.receber()
        j2 = self.conectar()
        for inicio in (j1.receber(), j2.receber()):
            self.assertEqual("".join(inicio["tabuleiro_proprio"]).count("N"), 4)
            self.assertEqual(inicio["tabuleiro_adversario"], VAZIO)


class TestEnquantoAguarda(BaseServidor):
    def test_disparar_antes_do_inicio_gera_partida_nao_iniciada(self):
        j1 = self.conectar()
        j1.receber()
        j1.enviar({"tipo": "DISPARAR", "coordenada": "B3"})
        resposta = j1.receber()
        self.assertEqual(resposta["tipo"], "ERRO")
        self.assertEqual(resposta["codigo"], "PARTIDA_NAO_INICIADA")
        self.esperar_estado(AGUARDANDO_SEGUNDO)

    def test_sair_enquanto_aguarda_libera_a_vaga_de_jogador_1(self):
        j1 = self.conectar()
        j1.receber()
        j1.enviar({"tipo": "SAIR"})
        j1.esperar_fechamento(self)  # SAIR não tem resposta; o servidor só fecha
        self.esperar_estado(SEM_JOGADORES)
        novo = self.conectar()
        self.assertEqual(novo.receber()["tipo"], "AGUARDE")  # é o novo jogador 1
        j2 = self.conectar()
        self.assertEqual(novo.receber()["jogador"], 1)
        self.assertEqual(j2.receber()["jogador"], 2)

    def test_queda_enquanto_aguarda_libera_a_vaga_de_jogador_1(self):
        j1 = self.conectar()
        j1.receber()
        j1.fechar()
        self.esperar_estado(SEM_JOGADORES)
        novo = self.conectar()
        self.assertEqual(novo.receber()["tipo"], "AGUARDE")

    def test_mensagem_invalida_e_tipo_desconhecido_nao_mudam_o_estado(self):
        j1 = self.conectar()
        j1.receber()
        j1.sock.sendall(b"isto nao e json\n")
        self.assertEqual(j1.receber()["codigo"], "MENSAGEM_INVALIDA")
        j1.enviar({"tipo": "INICIO"})
        self.assertEqual(j1.receber()["codigo"], "TIPO_DESCONHECIDO")
        self.esperar_estado(AGUARDANDO_SEGUNDO)
        j2 = self.conectar()
        self.assertEqual(j1.receber()["tipo"], "INICIO")
        self.assertEqual(j2.receber()["tipo"], "INICIO")


class TestDuranteAPartida(BaseServidor):
    def test_terceiro_cliente_recebe_servidor_cheio_e_e_desconectado(self):
        j1, j2 = self.iniciar_partida()
        terceiro = self.conectar()
        resposta = terceiro.receber()
        self.assertEqual(resposta["tipo"], "ERRO")
        self.assertEqual(resposta["codigo"], "SERVIDOR_CHEIO")
        terceiro.esperar_fechamento(self)
        self.esperar_estado(VEZ_J1)
        # A partida segue: os dois jogadores continuam sendo atendidos.
        j2.enviar({"tipo": "PING"})
        self.assertEqual(j2.receber()["codigo"], "TIPO_DESCONHECIDO")

    def test_sair_durante_a_partida_avisa_o_adversario(self):
        for quem_sai in (0, 1):
            with self.subTest(jogador=quem_sai + 1):
                jogadores = self.iniciar_partida()
                saiu, ficou = jogadores[quem_sai], jogadores[1 - quem_sai]
                saiu.enviar({"tipo": "SAIR"})
                self.assertEqual(
                    ficou.receber(),
                    {
                        "tipo": "FIM",
                        "motivo": "ADVERSARIO_SAIU",
                        "vencedor": None,
                        "mensagem": "O adversário saiu da partida.",
                    },
                )
                ficou.esperar_fechamento(self)
                saiu.esperar_fechamento(self)
                self.esperar_estado(SEM_JOGADORES)

    def test_queda_durante_a_partida_avisa_o_adversario(self):
        j1, j2 = self.iniciar_partida()
        j2.fechar()
        fim = j1.receber()
        self.assertEqual(fim["tipo"], "FIM")
        self.assertEqual(fim["motivo"], "ADVERSARIO_DESCONECTOU")
        self.assertIsNone(fim["vencedor"])
        j1.esperar_fechamento(self)
        self.esperar_estado(SEM_JOGADORES)

    def test_mensagem_incompleta_e_tratada_como_queda(self):
        j1, j2 = self.iniciar_partida()
        j1.sock.sendall(b'{"tipo":"DISP')  # sem a LF final
        j1.fechar()
        self.assertEqual(j2.receber()["motivo"], "ADVERSARIO_DESCONECTOU")

    def test_apos_o_fim_o_servidor_aceita_nova_partida(self):
        j1, j2 = self.iniciar_partida()
        j1.enviar({"tipo": "SAIR"})
        j2.receber()
        self.esperar_estado(SEM_JOGADORES)
        novo1, novo2 = self.iniciar_partida()
        self.esperar_estado(VEZ_J1)

    def test_erros_vao_so_para_quem_errou(self):
        j1, j2 = self.iniciar_partida()
        j2.sock.sendall(b"[1,2]\n")
        self.assertEqual(j2.receber()["codigo"], "MENSAGEM_INVALIDA")
        # j1 não recebeu nada: a próxima mensagem dele é a resposta ao próprio pedido.
        j1.enviar({"tipo": "OLA"})
        self.assertEqual(j1.receber()["codigo"], "TIPO_DESCONHECIDO")


class TestDisparos(BaseServidor):
    """Fase 6: linhas 6 a 9 da tabela 9.2 (navios da seção 10 do readme.md)."""

    def disparar(self, cliente, coordenada):
        cliente.enviar({"tipo": "DISPARAR", "coordenada": coordenada})

    def receber_atualizacoes(self, j1, j2):
        a1, a2 = j1.receber(), j2.receber()
        self.assertEqual((a1["tipo"], a2["tipo"]), ("ATUALIZACAO", "ATUALIZACAO"))
        for atualizacao in (a1, a2):
            self.assertNotIn("N", "".join(atualizacao["tabuleiro_adversario"]))
        return a1, a2

    def test_turno_completo_da_secao_10(self):
        j1, j2 = self.iniciar_partida()
        self.disparar(j1, "B3")
        a1, a2 = self.receber_atualizacoes(j1, j2)
        self.assertEqual(
            a1,
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
            a2,
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

    def test_disparo_fora_da_vez_gera_erro_so_para_quem_tentou(self):
        j1, j2 = self.iniciar_partida()
        self.disparar(j2, "A1")
        self.assertEqual(j2.receber()["codigo"], "FORA_DE_VEZ")
        # FORA_DE_VEZ vem antes da validação da coordenada (seção 8).
        self.disparar(j2, "Z9")
        self.assertEqual(j2.receber()["codigo"], "FORA_DE_VEZ")
        self.esperar_estado(VEZ_J1)
        # j1 não recebeu nada: a próxima mensagem dele é a do próprio disparo.
        self.disparar(j1, "C3")
        a1, _ = self.receber_atualizacoes(j1, j2)
        self.assertEqual((a1["coordenada"], a1["resultado"]), ("C3", "AGUA"))

    def test_coordenadas_invalidas_nao_mudam_a_vez(self):
        j1, j2 = self.iniciar_partida()
        for mensagem in (
            {"tipo": "DISPARAR", "coordenada": "Z9"},
            {"tipo": "DISPARAR", "coordenada": "b3"},
            {"tipo": "DISPARAR", "coordenada": "B10"},
            {"tipo": "DISPARAR", "coordenada": 7},
            {"tipo": "DISPARAR"},
        ):
            with self.subTest(mensagem=mensagem):
                j1.enviar(mensagem)
                erro = j1.receber()
                self.assertEqual(erro["codigo"], "COORDENADA_INVALIDA")
                self.assertEqual(erro["mensagem"], "Coordenada inválida. Use de A1 a E5.")
        self.esperar_estado(VEZ_J1)
        self.disparar(j1, "B3")
        a1, a2 = self.receber_atualizacoes(j1, j2)
        self.assertEqual(a1["resultado"], "ACERTO")  # tabuleiro não foi alterado

    def test_disparo_repetido(self):
        j1, j2 = self.iniciar_partida()
        self.disparar(j1, "B3")
        self.receber_atualizacoes(j1, j2)
        self.disparar(j2, "C3")
        self.receber_atualizacoes(j1, j2)
        self.disparar(j1, "B3")
        erro = j1.receber()
        self.assertEqual(erro["codigo"], "DISPARO_REPETIDO")
        self.assertEqual(erro["mensagem"], "Você já atirou em B3.")
        self.esperar_estado(VEZ_J1)

    def test_mesma_coordenada_nos_dois_tabuleiros_nao_e_repetida(self):
        j1, j2 = self.iniciar_partida()
        self.disparar(j1, "C3")
        self.receber_atualizacoes(j1, j2)
        self.disparar(j2, "C3")  # C3 no tabuleiro do jogador 1: primeira vez
        a1, a2 = self.receber_atualizacoes(j1, j2)
        self.assertEqual((a2["atirador"], a2["resultado"]), (2, "AGUA"))

    def jogar(self, j1, j2, jogadas):
        """Executa (atirador, coordenada, resultado esperado) e devolve a última dupla."""
        clientes = {1: j1, 2: j2}
        for atirador, coordenada, esperado in jogadas:
            self.disparar(clientes[atirador], coordenada)
            a1, a2 = self.receber_atualizacoes(j1, j2)
            for atualizacao in (a1, a2):
                self.assertEqual(
                    (atualizacao["atirador"], atualizacao["coordenada"], atualizacao["resultado"]),
                    (atirador, coordenada, esperado),
                )
        return a1, a2

    def test_partida_completa_com_vitoria_do_jogador_1(self):
        j1, j2 = self.iniciar_partida()
        a1, a2 = self.jogar(
            j1,
            j2,
            [
                (1, "D1", "ACERTO"), (2, "C1", "AGUA"),
                (1, "E1", "AFUNDOU"), (2, "C2", "AGUA"),
                (1, "B3", "ACERTO"), (2, "C3", "AGUA"),
                (1, "B4", "AFUNDOU"),
            ],
        )
        self.assertEqual((a1["sua_vez"], a2["sua_vez"]), (False, False))
        self.assertEqual(a1["navios_restantes"], {"proprio": 2, "adversario": 0})
        self.assertEqual(a2["navios_restantes"], {"proprio": 0, "adversario": 2})
        self.assertEqual(a1["tabuleiro_adversario"], ["~~~XX", "~~~~~", "~X~~~", "~X~~~", "~~~~~"])
        self.assertEqual(a2["tabuleiro_adversario"], ["~~O~~", "~~O~~", "~~O~~", "~~~~~", "~~~~~"])
        fim = {
            "tipo": "FIM",
            "motivo": "VITORIA",
            "vencedor": 1,
            "mensagem": "Jogador 1 afundou todos os navios do adversário.",
        }
        self.assertEqual(j1.receber(), fim)
        self.assertEqual(j2.receber(), fim)
        j1.esperar_fechamento(self)
        j2.esperar_fechamento(self)
        self.esperar_estado(SEM_JOGADORES)

    def test_vitoria_do_jogador_2(self):
        j1, j2 = self.iniciar_partida()
        self.jogar(
            j1,
            j2,
            [
                (1, "A5", "AGUA"), (2, "A1", "ACERTO"),
                (1, "B5", "AGUA"), (2, "B1", "AFUNDOU"),
                (1, "C5", "AGUA"), (2, "D4", "ACERTO"),
                (1, "E5", "AGUA"), (2, "D5", "AFUNDOU"),
            ],
        )
        for cliente in (j1, j2):
            fim = cliente.receber()
            self.assertEqual((fim["motivo"], fim["vencedor"]), ("VITORIA", 2))
            cliente.esperar_fechamento(self)

    def test_nova_partida_depois_de_uma_vitoria_comeca_do_zero(self):
        j1, j2 = self.iniciar_partida()
        self.jogar(
            j1,
            j2,
            [(1, "D1", "ACERTO"), (2, "C1", "AGUA"), (1, "E1", "AFUNDOU"),
             (2, "C2", "AGUA"), (1, "B3", "ACERTO"), (2, "C3", "AGUA"), (1, "B4", "AFUNDOU")],
        )
        j1.receber()
        j2.receber()
        self.esperar_estado(SEM_JOGADORES)
        n1, n2 = self.iniciar_partida()
        self.disparar(n1, "D1")  # mesma casa da partida anterior: não é repetida
        a1, _ = self.receber_atualizacoes(n1, n2)
        self.assertEqual(a1["resultado"], "ACERTO")
        self.assertEqual(a1["navios_restantes"], {"proprio": 2, "adversario": 2})

    def test_sair_no_meio_da_partida_com_disparos(self):
        j1, j2 = self.iniciar_partida()
        self.jogar(j1, j2, [(1, "D1", "ACERTO")])
        j2.enviar({"tipo": "SAIR"})
        self.assertEqual(j1.receber()["motivo"], "ADVERSARIO_SAIU")
        self.esperar_estado(SEM_JOGADORES)


if __name__ == "__main__":
    unittest.main()
