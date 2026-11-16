"""Testes do módulo protocolo.py usando socket.socketpair().

Cada teste cria um par de sockets conectados entre si: o que um lado envia, o outro
recebe. Assim o protocolo é testado em sockets reais, sem servidor nem cliente.

Executar a partir da raiz do projeto:
    python -m unittest discover -s tests -v
"""

import socket
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from protocolo import (  # noqa: E402
    TAMANHO_MAXIMO,
    ConexaoEncerrada,
    MensagemIncompleta,
    MensagemInvalida,
    ProtocoloError,
    enviar_mensagem,
    receber_mensagem,
)

DISPARO = {"tipo": "DISPARAR", "coordenada": "B3"}
DISPARO_BYTES = b'{"tipo":"DISPARAR","coordenada":"B3"}\n'


class BaseSocketPair(unittest.TestCase):
    """Cria `self.a` (quem envia) e `self.b` (quem recebe, com seu leitor)."""

    def setUp(self):
        self.a, self.b = socket.socketpair()
        # Rede de segurança: um erro de lógica vira falha do teste em vez de travar.
        self.b.settimeout(5)
        self.leitor = self.b.makefile("rb")  # uma vez por conexão
        self.addCleanup(self._fechar)

    def _fechar(self):
        self.leitor.close()
        self.b.close()
        self.a.close()


class TestEnvioERecebimento(BaseSocketPair):
    def test_envia_e_recebe_uma_mensagem(self):
        enviar_mensagem(self.a, DISPARO)
        self.assertEqual(receber_mensagem(self.leitor), DISPARO)

    def test_preserva_acentos_e_tipos_json(self):
        original = {"tipo": "ERRO", "mensagem": "Não é a sua vez, ação inválida", "n": 2, "ok": False, "x": None}
        enviar_mensagem(self.a, original)
        self.assertEqual(receber_mensagem(self.leitor), original)

    def test_formato_no_fio_e_json_utf8_compacto_com_uma_lf_no_final(self):
        enviar_mensagem(self.a, {"tipo": "ERRO", "mensagem": "ação\ninválida"})
        bruto = self.b.recv(4096)
        self.assertEqual(bruto, '{"tipo":"ERRO","mensagem":"ação\\ninválida"}\n'.encode("utf-8"))
        self.assertEqual(bruto.count(b"\n"), 1)  # a LF dentro do texto virou "\n" escapado

    def test_varias_mensagens_em_sequencia_mantem_a_ordem(self):
        for i in range(1, 6):
            enviar_mensagem(self.a, {"tipo": "N", "i": i})
        self.assertEqual([receber_mensagem(self.leitor)["i"] for _ in range(5)], [1, 2, 3, 4, 5])


class TestMensagensJuntas(BaseSocketPair):
    def test_duas_mensagens_enviadas_juntas(self):
        # Um único sendall com duas linhas: chegam no mesmo bloco de bytes.
        self.a.sendall(DISPARO_BYTES + b'{"tipo":"SAIR"}\n')
        self.assertEqual(receber_mensagem(self.leitor), DISPARO)
        self.assertEqual(receber_mensagem(self.leitor), {"tipo": "SAIR"})

    def test_mensagem_completa_seguida_do_comeco_da_proxima(self):
        self.a.sendall(DISPARO_BYTES + b'{"tipo":"SA')
        self.assertEqual(receber_mensagem(self.leitor), DISPARO)
        self.a.sendall(b'IR"}\n')
        self.assertEqual(receber_mensagem(self.leitor), {"tipo": "SAIR"})


class TestMensagemEmPartes(BaseSocketPair):
    def _receber_em_thread(self):
        resultado = {}
        concluido = threading.Event()

        def trabalho():
            try:
                resultado["mensagem"] = receber_mensagem(self.leitor)
            except Exception as erro:  # pragma: no cover - só em caso de falha
                resultado["erro"] = erro
            finally:
                concluido.set()

        thread = threading.Thread(target=trabalho, daemon=True)
        thread.start()
        return thread, resultado, concluido

    def test_mensagem_dividida_em_duas_partes(self):
        thread, resultado, concluido = self._receber_em_thread()
        self.a.sendall(DISPARO_BYTES[:15])
        self.assertFalse(concluido.wait(0.2), "não pode devolver antes da LF")
        self.a.sendall(DISPARO_BYTES[15:])
        thread.join(5)
        self.assertEqual(resultado.get("mensagem"), DISPARO, resultado)

    def test_mensagem_enviada_byte_a_byte(self):
        thread, resultado, concluido = self._receber_em_thread()
        for byte in DISPARO_BYTES:
            self.a.sendall(bytes([byte]))
            time.sleep(0.002)
        thread.join(5)
        self.assertEqual(resultado.get("mensagem"), DISPARO, resultado)

    def test_corte_no_meio_de_um_caractere_utf8_de_varios_bytes(self):
        bruto = '{"tipo":"ERRO","mensagem":"ação"}\n'.encode("utf-8")
        corte = bruto.index("ç".encode("utf-8")) + 1  # entre os 2 bytes do "ç"
        thread, resultado, concluido = self._receber_em_thread()
        self.a.sendall(bruto[:corte])
        self.assertFalse(concluido.wait(0.2))
        self.a.sendall(bruto[corte:])
        thread.join(5)
        self.assertEqual(resultado.get("mensagem"), {"tipo": "ERRO", "mensagem": "ação"}, resultado)


class TestMensagemInvalida(BaseSocketPair):
    def test_json_invalido(self):
        self.a.sendall(b"isto nao e json\n")
        with self.assertRaises(MensagemInvalida):
            receber_mensagem(self.leitor)

    def test_linhas_que_nao_sao_mensagens_validas(self):
        casos = {
            "linha vazia": b"\n",
            "json truncado": b'{"tipo":"DISPARAR"\n',
            "lista em vez de objeto": b'["DISPARAR"]\n',
            "numero": b"42\n",
            "objeto sem tipo": b'{"coordenada":"B3"}\n',
            "tipo nao textual": b'{"tipo":7}\n',
            "tipo vazio": b'{"tipo":""}\n',
            "NaN": b'{"tipo":"X","v":NaN}\n',
            "utf8 invalido": b'{"tipo":"X","v":"\xff\xfe"}\n',
        }
        for nome, linha in casos.items():
            with self.subTest(nome):
                self.a.sendall(linha)
                with self.assertRaises(MensagemInvalida):
                    receber_mensagem(self.leitor)

    def test_a_conexao_continua_utilizavel_apos_mensagem_invalida(self):
        self.a.sendall(b"lixo\n" + DISPARO_BYTES)
        with self.assertRaises(MensagemInvalida):
            receber_mensagem(self.leitor)
        self.assertEqual(receber_mensagem(self.leitor), DISPARO)

    def test_linha_maior_que_o_limite_e_descartada_e_o_leitor_realinha(self):
        lixo = b"x" * (TAMANHO_MAXIMO * 2 + 10)
        self.a.sendall(lixo + b"\n" + DISPARO_BYTES)
        with self.assertRaises(MensagemInvalida):
            receber_mensagem(self.leitor)
        self.assertEqual(receber_mensagem(self.leitor), DISPARO)

    def test_mensagem_de_exatamente_o_tamanho_maximo_e_aceita(self):
        prefixo = b'{"tipo":"X","v":"'
        sufixo = b'"}'
        corpo = prefixo + b"a" * (TAMANHO_MAXIMO - len(prefixo) - len(sufixo)) + sufixo
        self.assertEqual(len(corpo), TAMANHO_MAXIMO)
        # Envia em outra thread: 64 KiB podem exceder o buffer do socket.
        enviador = threading.Thread(target=self.a.sendall, args=(corpo + b"\n",), daemon=True)
        enviador.start()
        self.assertEqual(receber_mensagem(self.leitor)["tipo"], "X")
        enviador.join(5)

    def test_enviar_mensagem_invalida_levanta_erro_e_nao_envia_nada(self):
        invalidas = {
            "nao e dict": ["DISPARAR"],
            "sem tipo": {"coordenada": "B3"},
            "tipo nao textual": {"tipo": 1},
            "nao serializavel": {"tipo": "X", "v": object()},
            "NaN": {"tipo": "X", "v": float("nan")},
            "grande demais": {"tipo": "X", "v": "a" * (TAMANHO_MAXIMO + 1)},
        }
        for nome, mensagem in invalidas.items():
            with self.subTest(nome):
                with self.assertRaises(MensagemInvalida):
                    enviar_mensagem(self.a, mensagem)
        self.b.setblocking(False)
        with self.assertRaises(BlockingIOError):  # nada chegou ao outro lado
            self.b.recv(1)
        self.b.settimeout(5)


class TestConexaoEncerrada(BaseSocketPair):
    def test_conexao_encerrada_sem_dados_pendentes(self):
        self.a.close()
        with self.assertRaises(ConexaoEncerrada) as contexto:
            receber_mensagem(self.leitor)
        self.assertNotIsInstance(contexto.exception, MensagemIncompleta)

    def test_le_mensagens_pendentes_e_so_depois_informa_o_encerramento(self):
        self.a.sendall(DISPARO_BYTES)
        self.a.close()
        self.assertEqual(receber_mensagem(self.leitor), DISPARO)
        with self.assertRaises(ConexaoEncerrada):
            receber_mensagem(self.leitor)

    def test_continua_informando_encerramento_em_leituras_seguintes(self):
        self.a.close()
        for _ in range(3):
            with self.assertRaises(ConexaoEncerrada):
                receber_mensagem(self.leitor)

    def test_enviar_para_conexao_encerrada(self):
        # O makefile mantém o socket aberto até o leitor ser fechado; feche os dois.
        self.leitor.close()
        self.b.close()
        # O TCP pode aceitar o primeiro envio antes de descobrir que a outra ponta fechou.
        with self.assertRaises(ConexaoEncerrada):
            for _ in range(200):
                enviar_mensagem(self.a, DISPARO)
                time.sleep(0.005)


class TestMensagemInterrompida(BaseSocketPair):
    def test_conexao_encerrada_antes_da_quebra_de_linha(self):
        self.a.sendall(DISPARO_BYTES[:-1])  # sem a LF final
        self.a.close()
        with self.assertRaises(MensagemIncompleta):
            receber_mensagem(self.leitor)

    def test_mensagem_incompleta_e_tratada_como_desconexao(self):
        self.a.sendall(b'{"tipo":"DISP')
        self.a.close()
        with self.assertRaises(ConexaoEncerrada):  # uma única captura cobre os dois casos
            receber_mensagem(self.leitor)

    def test_bytes_parciais_nunca_viram_mensagem(self):
        self.a.sendall(DISPARO_BYTES + b'{"tipo":"SAIR"}')  # a segunda não tem LF
        self.a.close()
        self.assertEqual(receber_mensagem(self.leitor), DISPARO)
        with self.assertRaises(MensagemIncompleta):
            receber_mensagem(self.leitor)
        with self.assertRaises(ConexaoEncerrada):
            receber_mensagem(self.leitor)


class TestHierarquiaDeErros(unittest.TestCase):
    def test_hierarquia(self):
        self.assertTrue(issubclass(MensagemIncompleta, ConexaoEncerrada))
        self.assertTrue(issubclass(ConexaoEncerrada, ProtocoloError))
        self.assertTrue(issubclass(MensagemInvalida, ProtocoloError))
        self.assertFalse(issubclass(MensagemInvalida, ConexaoEncerrada))


if __name__ == "__main__":
    unittest.main()
