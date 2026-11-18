"""Testes da janela (cliente_gui.py) contra o servidor real.

Cada teste abre uma janela Tk (oculta), liga-a a um servidor em 127.0.0.1 com navios
fixos e exerce a interface como o usuário: preenche campos, aperta botões
(`Button.invoke()`, o mesmo comando do clique) e confere o estado dos widgets. O
segundo jogador é um socket cru, ou, em um teste, o `cliente.py` de terminal como
processo de verdade. São pulados se o Tkinter ou uma tela não estiverem disponíveis.

Tabuleiros fixos (iguais aos de test_integracao.py): o jogador 1 tem navios em A1-B1 e
D4-D5; o jogador 2, em D1-E1 e B3-B4. A janela é sempre o jogador 1 (conecta primeiro).
"""

from __future__ import annotations

import itertools
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

try:
    import tkinter as tk
    _teste_tk = tk.Tk()
    _teste_tk.destroy()
    TK_OK = True
except Exception:  # noqa: BLE001 - sem Tkinter ou sem tela
    tk = None  # type: ignore[assignment]
    TK_OK = False

from protocolo import enviar_mensagem, receber_mensagem  # noqa: E402
from servidor import SEM_JOGADORES, Servidor  # noqa: E402
from tabuleiro import Tabuleiro  # noqa: E402

if TK_OK:
    import cliente_gui  # noqa: E402
    from nucleo_gui import AFUNDOU, EM_ANDAMENTO, ENCERRADA, AGUARDANDO  # noqa: E402

NAVIOS_J1 = [("A1", "B1"), ("D4", "D5")]
NAVIOS_J2 = [("D1", "E1"), ("B3", "B4")]
PRAZO = 6.0


def estado(widget) -> str:
    return str(widget.cget("state"))


@unittest.skipUnless(TK_OK, "Tkinter ou tela indisponível")
class BaseGUI(unittest.TestCase):
    def setUp(self) -> None:
        navios = itertools.cycle([NAVIOS_J1, NAVIOS_J2])
        self.servidor = Servidor("127.0.0.1", 0, criar_tabuleiro=lambda: Tabuleiro(next(navios)))
        _, self.porta = self.servidor.iniciar()
        self.thread = threading.Thread(target=self.servidor.servir_para_sempre, daemon=True)
        self.thread.start()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = cliente_gui.App(self.root, "127.0.0.1", self.porta)
        self.janela_aberta = True
        self.raw: list[tuple[socket.socket, object]] = []
        self.addCleanup(self._limpar)

    def _limpar(self) -> None:
        if self.janela_aberta:
            self.app._abandonar_sessao(enviar_sair=False)
            self.app._destruir()
        for sock, leitor in self.raw:
            sock.close()
        self.servidor.encerrar()
        self.thread.join(PRAZO)

    # --- helpers ---------------------------------------------------------

    def esperar(self, condicao, descricao: str = "condição") -> None:
        limite = time.monotonic() + PRAZO
        while time.monotonic() < limite:
            self.root.update()
            if condicao():
                return
            time.sleep(0.01)
        self.fail(f"tempo esgotado esperando: {descricao}")

    def esperar_servidor(self, esperado: str) -> None:
        self.esperar(lambda: self.servidor.estado == esperado, f"servidor em {esperado}")

    def conectar_gui(self) -> None:
        self.app._conectar()

    def jogador_raw(self):
        sock = socket.create_connection(("127.0.0.1", self.porta), timeout=PRAZO)
        par = (sock, sock.makefile("rb"))
        self.raw.append(par)
        return par

    def iniciar_partida(self):
        """A janela vira o jogador 1; devolve (sock, leitor) do jogador 2, após o INICIO."""
        self.conectar_gui()
        self.esperar(lambda: self.app.modelo.fase == AGUARDANDO, "AGUARDE")
        sock, leitor = self.jogador_raw()
        self.assertEqual(receber_mensagem(leitor)["tipo"], "INICIO")
        self.esperar(lambda: self.app.modelo.fase == EM_ANDAMENTO, "INICIO")
        return sock, leitor

    def casa(self, coordenada: str):
        linha, coluna = int(coordenada[1]) - 1, "ABCDE".index(coordenada[0])
        return self.app.disparos["celulas"][linha][coluna]

    def minha_casa(self, coordenada: str):
        linha, coluna = int(coordenada[1]) - 1, "ABCDE".index(coordenada[0])
        return self.app.proprio["celulas"][linha][coluna]

    def clicar(self, coordenada: str) -> None:
        self.casa(coordenada).invoke()

    def clicar_e_esperar_resposta(self, coordenada: str, leitor) -> dict:
        """Clica, recebe no adversário a ATUALIZACAO e espera a janela processar a sua."""
        self.clicar(coordenada)
        atualizacao = receber_mensagem(leitor)
        self.esperar(lambda: self.app.modelo.pendente is None, "resposta ao disparo")
        return atualizacao

    def atirar_como_adversario(self, sock, leitor, coordenada: str) -> None:
        enviar_mensagem(sock, {"tipo": "DISPARAR", "coordenada": coordenada})
        receber_mensagem(leitor)
        self.esperar(lambda: self.app.modelo.ultima_jogada.startswith("O adversário"), "jogada do adversário")

    def habilitadas(self) -> list[str]:
        return [f"{'ABCDE'[c]}{l + 1}" for l in range(5) for c in range(5)
                if estado(self.app.disparos["celulas"][l][c]) == "normal"]


class TestTelaDeConexao(BaseGUI):
    def test_valores_iniciais(self) -> None:
        self.assertEqual(self.app.var_endereco.get(), "127.0.0.1")
        self.assertEqual(self.app.var_porta.get(), str(self.porta))
        self.assertEqual(estado(self.app.botao_conectar), "normal")
        self.assertEqual(estado(self.app.botao_cancelar), "disabled")

    def test_porta_padrao_e_5050(self) -> None:
        raiz = tk.Tk()
        raiz.withdraw()
        try:
            app = cliente_gui.App(raiz)
            self.assertEqual((app.var_endereco.get(), app.var_porta.get()), ("127.0.0.1", "5050"))
        finally:
            app._destruir()

    def test_endereco_vazio_mostra_erro_e_nao_conecta(self) -> None:
        self.app.var_endereco.set("  ")
        self.app._conectar()
        self.assertIn("endereço", str(self.app.rotulo_conexao.cget("text")))
        self.assertIsNone(self.app._sessao)

    def test_porta_invalida_mostra_erro(self) -> None:
        self.app.var_porta.set("99999")
        self.app._conectar()
        self.assertIn("porta", str(self.app.rotulo_conexao.cget("text")).lower())
        self.assertIsNone(self.app._sessao)

    def test_servidor_fora_do_ar_mostra_erro_e_libera_os_campos(self) -> None:
        livre = socket.socket()
        livre.bind(("127.0.0.1", 0))
        porta = livre.getsockname()[1]
        livre.close()
        self.app.var_porta.set(str(porta))
        self.conectar_gui()
        self.assertEqual(estado(self.app.botao_conectar), "disabled")  # conexão em andamento
        self.assertEqual(estado(self.app.botao_cancelar), "normal")
        self.esperar(lambda: self.app._sessao is None, "falha de conexão")
        self.assertIn("127.0.0.1", str(self.app.rotulo_conexao.cget("text")))
        self.assertEqual(str(self.app.rotulo_conexao.cget("style")), "Erro.TLabel")
        self.assertEqual(estado(self.app.botao_conectar), "normal")
        self.assertEqual(estado(self.app.entrada_endereco), "normal")

    def test_cancelar_conexao(self) -> None:
        self.conectar_gui()
        self.app._cancelar_conexao()
        self.assertIsNone(self.app._sessao)
        self.assertEqual(estado(self.app.botao_conectar), "normal")
        self.root.update()
        self.assertEqual(self.app.modelo.fase, "CONECTADO")  # nada chegou ao modelo

    def test_endereco_estilo_tunel_e_normalizado(self) -> None:
        self.app.var_endereco.set(f"tcp://127.0.0.1:{self.porta}")
        self.app.var_porta.set("1")  # a porta embutida vale mais
        self.conectar_gui()
        self.assertEqual(self.app.var_endereco.get(), "127.0.0.1")
        self.assertEqual(self.app.var_porta.get(), str(self.porta))
        self.esperar(lambda: self.app.modelo.fase == AGUARDANDO, "AGUARDE via endereço de túnel")

    def test_servidor_cheio_volta_para_a_tela_de_conexao_com_erro(self) -> None:
        self.iniciar_partida()
        outra = tk.Tk()
        outra.withdraw()
        app2 = cliente_gui.App(outra, "127.0.0.1", self.porta)
        try:
            app2._conectar()
            limite = time.monotonic() + PRAZO
            while app2._sessao is not None and time.monotonic() < limite:
                outra.update()
                time.sleep(0.01)
            self.assertIsNone(app2._sessao)
            self.assertIn("partida em andamento", str(app2.rotulo_conexao.cget("text")))
            self.assertFalse(app2._partida_aberta)
        finally:
            app2._destruir()


class TestAguardando(BaseGUI):
    def test_aguardando_mostra_mensagem_e_permite_sair(self) -> None:
        self.conectar_gui()
        self.esperar(lambda: self.app.modelo.fase == AGUARDANDO, "AGUARDE")
        self.assertTrue(self.app._partida_aberta)
        self.assertEqual(str(self.app.rotulo_status.cget("text")), "Aguardando outro jogador...")
        self.assertEqual(estado(self.app.botao_sair), "normal")
        self.assertEqual(estado(self.app.botao_voltar), "disabled")
        self.assertEqual(self.habilitadas(), [])  # sem partida, nenhum clique
        self.app._sair()  # sem confirmação: não há partida em andamento
        self.assertFalse(self.app._partida_aberta)
        self.esperar_servidor(SEM_JOGADORES)  # a vaga foi liberada (SAIR enviado)
        self.assertEqual(estado(self.app.botao_conectar), "normal")

    def test_reconectar_depois_de_sair(self) -> None:
        self.conectar_gui()
        self.esperar(lambda: self.app.modelo.fase == AGUARDANDO, "AGUARDE")
        self.app._sair()
        self.esperar_servidor(SEM_JOGADORES)
        self.conectar_gui()
        self.esperar(lambda: self.app.modelo.fase == AGUARDANDO, "AGUARDE na nova conexão")
        self.assertTrue(self.app._partida_aberta)


class TestPartida(BaseGUI):
    def test_inicio_mostra_identificacao_vez_e_tabuleiro(self) -> None:
        self.iniciar_partida()
        self.assertEqual(str(self.app.rotulo_jogador.cget("text")), "Você é o Jogador 1")
        self.assertIn("Sua vez", str(self.app.rotulo_status.cget("text")))
        self.assertEqual(str(self.minha_casa("A1").cget("text")), "■")  # meu navio
        self.assertEqual(str(self.minha_casa("C1").cget("text")), "~")
        self.assertEqual(str(self.casa("B3").cget("text")), "?")
        self.assertEqual(len(self.habilitadas()), 25)  # minha vez: todas clicáveis
        self.assertEqual(estado(self.app.botao_sair), "normal")

    def test_clique_envia_disparo_bloqueia_e_passa_a_vez(self) -> None:
        sock, leitor = self.iniciar_partida()
        self.clicar("B3")
        # logo após o clique: nada é clicável até a resposta do servidor
        self.assertEqual(self.habilitadas(), [])
        self.assertIn("B3", str(self.app.rotulo_status.cget("text")))
        mensagem = receber_mensagem(leitor)
        self.assertEqual((mensagem["tipo"], mensagem["resultado"], mensagem["atirador"]), ("ATUALIZACAO", "ACERTO", 1))
        self.esperar(lambda: self.app.modelo.pendente is None, "ATUALIZACAO")
        self.assertEqual(str(self.casa("B3").cget("text")), "✕")
        self.assertEqual(self.habilitadas(), [])  # agora é a vez do adversário
        self.assertIn("Vez do adversário", str(self.app.rotulo_status.cget("text")))
        self.assertIn("B3", str(self.app.rotulo_jogada.cget("text")))
        self.assertEqual(str(self.app.disparos["contagem"].cget("text")), "Navios do adversário: 2 de 2 restantes")

    def test_clique_duplo_so_envia_um_disparo(self) -> None:
        sock, leitor = self.iniciar_partida()
        self.clicar("B3")
        self.app._ao_clicar("C3")   # segundo clique antes da resposta
        self.app._ao_clicar("B3")
        self.assertEqual(receber_mensagem(leitor)["coordenada"], "B3")
        self.esperar(lambda: self.app.modelo.pendente is None, "ATUALIZACAO")
        sock.settimeout(0.3)
        with self.assertRaises(TimeoutError):  # nenhuma outra ATUALIZACAO foi produzida
            receber_mensagem(leitor)

    def test_vez_do_adversario_e_volta(self) -> None:
        sock, leitor = self.iniciar_partida()
        self.clicar_e_esperar_resposta("C3", leitor)  # água
        self.assertEqual(str(self.casa("C3").cget("text")), "○")
        self.atirar_como_adversario(sock, leitor, "A1")  # acerta o navio A1-B1
        self.assertEqual(str(self.minha_casa("A1").cget("text")), "✕")
        self.assertIn("Sua vez", str(self.app.rotulo_status.cget("text")))
        self.assertNotIn("C3", self.habilitadas())  # já atacada
        self.assertEqual(len(self.habilitadas()), 24)

    def test_casa_ja_atacada_nunca_e_habilitada(self) -> None:
        sock, leitor = self.iniciar_partida()
        self.clicar_e_esperar_resposta("C3", leitor)
        self.atirar_como_adversario(sock, leitor, "C1")
        self.assertEqual(estado(self.casa("C3")), "disabled")
        self.app._ao_clicar("C3")  # mesmo forçando, o modelo recusa
        self.assertIsNone(self.app.modelo.pendente)

    def test_erro_do_servidor_libera_a_interface(self) -> None:
        sock, leitor = self.iniciar_partida()
        # Disparo "enviado" só no modelo, sem ir ao servidor: assim a resposta ERRO injetada
        # é a única que chega (o servidor real só recusaria um clique de interface em corrida).
        self.app.modelo.registrar_disparo("B3")
        self.app._renderizar()
        self.assertEqual(self.habilitadas(), [])
        self.app._eventos.put((self.app._geracao, "mensagem",
                               {"tipo": "ERRO", "codigo": "FORA_DE_VEZ", "mensagem": "Não é a sua vez de jogar."}))
        self.esperar(lambda: self.app.modelo.pendente is None, "tratamento do ERRO")
        self.assertEqual(str(self.app.rotulo_aviso.cget("text")), "Não é a sua vez de jogar.")
        self.assertEqual(str(self.app.rotulo_aviso.cget("style")), "AvisoErro.TLabel")
        self.assertEqual(self.app.modelo.fase, EM_ANDAMENTO)
        self.assertEqual(len(self.habilitadas()), 25)  # utilizável de novo

    def test_afundou_e_marcado_na_casa_do_golpe(self) -> None:
        sock, leitor = self.iniciar_partida()
        self.clicar_e_esperar_resposta("B3", leitor)
        self.atirar_como_adversario(sock, leitor, "C1")
        self.clicar_e_esperar_resposta("B4", leitor)  # afunda o navio B3-B4
        self.assertEqual(self.app.modelo.tipo_casa(False, 3, 1), AFUNDOU)
        self.assertEqual(str(self.casa("B4").cget("text")), "✖")
        self.assertEqual(str(self.casa("B4").cget("style")), "afundou.Celula.TButton")
        self.assertEqual(str(self.app.disparos["contagem"].cget("text")), "Navios do adversário: 1 de 2 restantes")
        self.assertIn("afundou", str(self.app.rotulo_jogada.cget("text")))

    def _jogar_ate_vitoria(self, sock, leitor) -> None:
        """A janela afunda os dois navios do jogador 2; o adversário só erra."""
        for meu_tiro, tiro_adversario in (("D1", "C1"), ("E1", "C2"), ("B3", "C3")):
            self.clicar_e_esperar_resposta(meu_tiro, leitor)
            self.atirar_como_adversario(sock, leitor, tiro_adversario)
        self.clicar("B4")
        receber_mensagem(leitor)  # ATUALIZACAO final
        receber_mensagem(leitor)  # FIM

    def test_vitoria(self) -> None:
        sock, leitor = self.iniciar_partida()
        self._jogar_ate_vitoria(sock, leitor)
        self.esperar(lambda: self.app.modelo.fase == ENCERRADA, "FIM")
        self.assertEqual(str(self.app.rotulo_status.cget("text")), "Vitória!")
        self.assertEqual(str(self.app.rotulo_status.cget("style")), "vitoria.Status.TLabel")
        self.assertEqual(self.habilitadas(), [])
        self.assertEqual(estado(self.app.botao_sair), "disabled")
        self.assertEqual(estado(self.app.botao_voltar), "normal")
        self.assertEqual(str(self.app.disparos["contagem"].cget("text")), "Navios do adversário: 0 de 2 restantes")
        # a conexão que o servidor fechou depois do FIM não vira "conexão perdida"
        self.root.update()
        time.sleep(0.2)
        self.root.update()
        self.assertEqual(str(self.app.rotulo_status.cget("text")), "Vitória!")

    def test_derrota(self) -> None:
        sock, leitor = self.iniciar_partida()
        for meu_tiro, tiro_adversario in (("E5", "A1"), ("E4", "B1"), ("E3", "D4")):
            self.clicar_e_esperar_resposta(meu_tiro, leitor)
            self.atirar_como_adversario(sock, leitor, tiro_adversario)
        self.clicar_e_esperar_resposta("E2", leitor)
        enviar_mensagem(sock, {"tipo": "DISPARAR", "coordenada": "D5"})
        self.esperar(lambda: self.app.modelo.fase == ENCERRADA, "FIM por derrota")
        self.assertEqual(str(self.app.rotulo_status.cget("text")), "Derrota")
        self.assertEqual(estado(self.app.botao_voltar), "normal")

    def test_voltar_para_conexao_e_jogar_de_novo(self) -> None:
        sock, leitor = self.iniciar_partida()
        self._jogar_ate_vitoria(sock, leitor)
        self.esperar(lambda: self.app.modelo.fase == ENCERRADA, "FIM")
        self.esperar_servidor(SEM_JOGADORES)
        self.app.botao_voltar.invoke()
        self.assertFalse(self.app._partida_aberta)
        self.assertEqual(estado(self.app.botao_conectar), "normal")
        self.assertEqual(self.app.modelo.fase, "CONECTADO")
        self.conectar_gui()
        self.esperar(lambda: self.app.modelo.fase == AGUARDANDO, "nova partida")
        self.assertEqual(self.app.modelo.afundou_adversario, set())

    def test_adversario_sai(self) -> None:
        sock, leitor = self.iniciar_partida()
        enviar_mensagem(sock, {"tipo": "SAIR"})
        self.esperar(lambda: self.app.modelo.fase == ENCERRADA, "FIM ADVERSARIO_SAIU")
        self.assertEqual(str(self.app.rotulo_status.cget("text")), "O adversário saiu")
        self.assertEqual(estado(self.app.botao_voltar), "normal")
        self.assertEqual(self.habilitadas(), [])

    def test_adversario_cai(self) -> None:
        sock, leitor = self.iniciar_partida()
        leitor.close()
        sock.close()
        self.esperar(lambda: self.app.modelo.fase == ENCERRADA, "FIM ADVERSARIO_DESCONECTOU")
        self.assertEqual(str(self.app.rotulo_status.cget("text")), "O adversário desconectou")

    def test_fim_chega_com_o_usuario_parado_na_tela(self) -> None:
        # Sem nenhuma interação: o FIM é processado sozinho pelo after().
        sock, leitor = self.iniciar_partida()
        enviar_mensagem(sock, {"tipo": "SAIR"})
        limite = time.monotonic() + PRAZO
        while time.monotonic() < limite and self.app.modelo.fase != ENCERRADA:
            self.root.update()
            time.sleep(0.01)
        self.assertEqual(self.app.modelo.fase, ENCERRADA)

    def test_queda_do_servidor(self) -> None:
        self.iniciar_partida()
        self.servidor.encerrar()
        self.esperar(lambda: self.app.modelo.fase == ENCERRADA, "conexão perdida")
        self.assertEqual(str(self.app.rotulo_status.cget("text")), "Conexão perdida")
        self.assertEqual(estado(self.app.botao_voltar), "normal")
        self.assertEqual(estado(self.app.botao_sair), "disabled")

    def test_queda_do_servidor_enquanto_aguarda(self) -> None:
        self.conectar_gui()
        self.esperar(lambda: self.app.modelo.fase == AGUARDANDO, "AGUARDE")
        self.servidor.encerrar()
        self.esperar(lambda: self.app.modelo.fase == ENCERRADA, "conexão perdida")
        self.assertEqual(estado(self.app.botao_voltar), "normal")

    def test_sair_durante_a_partida_avisa_o_adversario(self) -> None:
        sock, leitor = self.iniciar_partida()
        perguntas: list[str] = []
        self.app._confirmar = lambda titulo, texto: perguntas.append(titulo) or True
        self.app.botao_sair.invoke()
        self.assertEqual(perguntas, ["Sair da partida"])
        fim = receber_mensagem(leitor)
        self.assertEqual((fim["tipo"], fim["motivo"]), ("FIM", "ADVERSARIO_SAIU"))
        self.assertFalse(self.app._partida_aberta)
        self.esperar_servidor(SEM_JOGADORES)

    def test_sair_cancelado_na_confirmacao_continua_na_partida(self) -> None:
        self.iniciar_partida()
        self.app._confirmar = lambda *_a: False
        self.app.botao_sair.invoke()
        self.assertTrue(self.app._partida_aberta)
        self.assertEqual(self.app.modelo.fase, EM_ANDAMENTO)

    def test_fechar_a_janela_durante_a_partida_envia_sair_e_encerra_a_rede(self) -> None:
        sock, leitor = self.iniciar_partida()
        sessao = self.app._sessao
        self.app._confirmar = lambda *_a: True
        self.app._ao_fechar()
        self.janela_aberta = False
        with self.assertRaises(tk.TclError):
            self.root.winfo_exists()
        fim = receber_mensagem(leitor)
        self.assertEqual((fim["tipo"], fim["motivo"]), ("FIM", "ADVERSARIO_SAIU"))
        self.assertTrue(sessao.encerrada)
        sessao._thread.join(PRAZO)
        self.assertFalse(sessao._thread.is_alive())

    def test_fechar_a_janela_na_tela_de_conexao(self) -> None:
        self.app._ao_fechar()
        self.janela_aberta = False
        with self.assertRaises(tk.TclError):
            self.root.winfo_exists()

    def test_fechar_a_janela_enquanto_aguarda_libera_a_vaga(self) -> None:
        self.conectar_gui()
        self.esperar(lambda: self.app.modelo.fase == AGUARDANDO, "AGUARDE")
        self.app._ao_fechar()  # sem confirmação: não há partida em andamento
        self.janela_aberta = False
        limite = time.monotonic() + PRAZO
        while self.servidor.estado != SEM_JOGADORES and time.monotonic() < limite:
            time.sleep(0.01)
        self.assertEqual(self.servidor.estado, SEM_JOGADORES)

    def test_eventos_atrasados_da_conexao_anterior_sao_descartados(self) -> None:
        sock, leitor = self.iniciar_partida()
        antiga = self.app._geracao
        self.app._confirmar = lambda *_a: True
        self.app._sair()
        self.esperar_servidor(SEM_JOGADORES)
        self.conectar_gui()
        self.esperar(lambda: self.app.modelo.fase == AGUARDANDO, "nova conexão")
        # chegam, atrasados, FIM e ATUALIZACAO da conexão anterior
        self.app._eventos.put((antiga, "mensagem", {"tipo": "FIM", "motivo": "VITORIA", "vencedor": 2, "mensagem": ""}))
        self.app._eventos.put((antiga, "encerrada", None))
        self.app._eventos.put((antiga, "falha_conexao", "erro velho"))
        for _ in range(5):
            self.root.update()
            time.sleep(0.05)
        self.assertEqual(self.app.modelo.fase, AGUARDANDO)
        self.assertIsNotNone(self.app._sessao)
        self.assertNotIn("erro velho", str(self.app.rotulo_conexao.cget("text")))

    def test_mensagem_invalida_do_servidor_encerra_com_aviso(self) -> None:
        self.iniciar_partida()
        self.app._eventos.put((self.app._geracao, "mensagem", {"tipo": "ATUALIZACAO"}))  # campos ausentes
        self.esperar(lambda: self.app.modelo.fase == ENCERRADA, "falha de protocolo")
        self.assertEqual(str(self.app.rotulo_status.cget("text")), "Erro de comunicação")
        self.assertEqual(estado(self.app.botao_voltar), "normal")


@unittest.skipUnless(TK_OK, "Tkinter ou tela indisponível")
class TestGuiComClienteDeTerminal(BaseGUI):
    """Partida completa: janela (jogador 1) contra `cliente.py` real (jogador 2)."""

    def test_partida_ate_o_fim(self) -> None:
        self.conectar_gui()
        self.esperar(lambda: self.app.modelo.fase == AGUARDANDO, "AGUARDE")
        ambiente = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        terminal = subprocess.Popen(
            [sys.executable, "-u", str(RAIZ / "cliente.py"), "127.0.0.1", str(self.porta)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=RAIZ, env=ambiente, text=True, encoding="utf-8",
        )
        self.addCleanup(lambda: terminal.poll() is None and terminal.kill())
        self.esperar(lambda: self.app.modelo.fase == EM_ANDAMENTO, "INICIO")
        # O terminal só lê uma linha quando é a vez dele: as respostas vão na fila do stdin.
        terminal.stdin.write("C1\nC2\nC3\n")
        terminal.stdin.flush()
        for tiro in ("D1", "E1", "B3"):
            self.clicar(tiro)
            self.esperar(lambda: self.app.modelo.pendente is None, f"resposta a {tiro}")
            self.assertIn(self.casa(tiro).cget("text"), ("✕", "✖"))  # acerto ou afundou
            # espera a jogada do terminal chegar e devolver a vez
            self.esperar(lambda: self.app.modelo.sua_vez, "vez de volta")
        self.assertEqual(str(self.minha_casa("C3").cget("text")), "○")  # tiros na água do terminal
        self.clicar("B4")
        self.esperar(lambda: self.app.modelo.fase == ENCERRADA, "FIM")
        self.assertEqual(str(self.app.rotulo_status.cget("text")), "Vitória!")
        saida, _ = terminal.communicate(timeout=PRAZO * 2)
        self.assertEqual(terminal.returncode, 0, saida)
        self.assertIn("[FIM]", saida)
        self.assertIn("afundou todos os navios", saida)


if __name__ == "__main__":
    unittest.main()
