"""Camada de mensagens do protocolo de aplicação (compartilhada por cliente e servidor).

Este módulo cuida somente de *como* uma mensagem trafega pelo socket TCP:

    - cada mensagem é um objeto JSON codificado em UTF-8;
    - cada mensagem termina com uma quebra de linha LF (b"\\n");
    - o TCP é um fluxo de bytes sem fronteiras, então a LF é o que separa uma
      mensagem da próxima.

Ele NÃO conhece as regras da Batalha Naval: não sabe o que é DISPARAR, tabuleiro
ou turno. A única exigência sobre o conteúdo é o envelope comum a todas as
mensagens: um objeto JSON com o campo "tipo" (texto não vazio). O significado de
cada tipo está descrito no readme.md.

Uso:

    sock = socket.create_connection((host, porta))   # ou o socket aceito pelo servidor
    leitor = sock.makefile("rb")                      # UMA vez por conexão; reutilize sempre

    enviar_mensagem(sock, {"tipo": "DISPARAR", "coordenada": "B3"})
    mensagem = receber_mensagem(leitor)

Comportamento de receber_mensagem (resumo; detalhes no readme.md):

    situação                                          resultado
    ------------------------------------------------  ------------------------------
    linha completa com JSON válido                    devolve o dict
    mensagem chegou em partes                         espera até a LF e devolve o dict
    duas mensagens chegaram juntas                    uma por chamada, na ordem
    conexão encerrada sem bytes pendentes             ConexaoEncerrada
    conexão encerrada no meio de uma linha (sem LF)   MensagemIncompleta (é ConexaoEncerrada)
    linha que não é uma mensagem válida               MensagemInvalida (conexão segue utilizável)

Observações importantes:

    - Não use sock.recv() na mesma conexão em que usa o leitor: o leitor guarda
      bytes em buffer e a próxima mensagem pode já estar lá dentro.
    - Evite sock.settimeout() em sockets que têm leitor makefile: se estourar o
      tempo, o buffer do leitor pode ficar inconsistente (aviso da documentação
      do Python). Use sockets bloqueantes.
    - sendall() não é atômico entre threads. Se mais de uma thread puder escrever
      no mesmo socket, proteja enviar_mensagem com um Lock por socket.
"""

import json
from typing import Any, BinaryIO

CODIFICACAO = "utf-8"
DELIMITADOR = b"\n"

# Tamanho máximo do corpo JSON (em bytes, sem contar a LF). Protege o servidor de
# uma linha sem fim; as mensagens do jogo têm bem menos de 1 KiB.
TAMANHO_MAXIMO = 64 * 1024


class ProtocoloError(Exception):
    """Base de todos os erros deste módulo."""


class ConexaoEncerrada(ProtocoloError):
    """A outra ponta fechou a conexão (ou ela caiu).

    Para o jogo, significa "esse jogador desconectou". Não há mais o que ler ou
    escrever nesse socket.
    """


class MensagemIncompleta(ConexaoEncerrada):
    """A conexão terminou no meio de uma mensagem (bytes recebidos sem a LF final).

    É uma ConexaoEncerrada: trate como desconexão. Os bytes parciais são
    descartados, nunca interpretados como mensagem.
    """


class MensagemInvalida(ProtocoloError):
    """A linha chegou inteira, mas não é uma mensagem válida do protocolo.

    Causas: UTF-8 inválido, JSON inválido, linha vazia, JSON que não é um objeto,
    objeto sem "tipo" textual, linha maior que TAMANHO_MAXIMO. Também é levantada
    por enviar_mensagem quando o chamador tenta enviar algo inválido (erro de
    programação).

    Ao ser levantada em receber_mensagem, a linha defeituosa já foi consumida:
    o leitor está alinhado com a próxima mensagem e a conexão continua utilizável.
    """


def _validar_envelope(mensagem: Any) -> None:
    if not isinstance(mensagem, dict):
        raise MensagemInvalida("a mensagem deve ser um objeto JSON (dict)")
    tipo = mensagem.get("tipo")
    if not isinstance(tipo, str) or not tipo:
        raise MensagemInvalida('a mensagem deve ter o campo "tipo" (texto não vazio)')


def _constante_invalida(nome: str) -> Any:
    # json.loads aceitaria NaN/Infinity, que não existem no JSON padrão.
    raise ValueError(f"constante JSON não permitida: {nome}")


def enviar_mensagem(sock, mensagem: dict[str, Any]) -> None:
    """Serializa `mensagem` e envia como uma linha JSON UTF-8 terminada em LF.

    Usa sendall(), então ou todos os bytes são entregues ao TCP ou há exceção.

    Levanta:
        MensagemInvalida: `mensagem` não é um dict com "tipo", não é serializável
            em JSON ou passa de TAMANHO_MAXIMO. Nada é enviado.
        ConexaoEncerrada: a outra ponta já fechou/derrubou a conexão.
    """
    _validar_envelope(mensagem)
    try:
        # O JSON compacto nunca contém LF crua: quebras dentro de textos viram "\n".
        corpo = json.dumps(
            mensagem, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode(CODIFICACAO)
    except (TypeError, ValueError) as erro:
        raise MensagemInvalida(f"mensagem não serializável em JSON: {erro}") from erro
    if len(corpo) > TAMANHO_MAXIMO:
        raise MensagemInvalida(
            f"mensagem com {len(corpo)} bytes excede o máximo de {TAMANHO_MAXIMO}"
        )
    try:
        sock.sendall(corpo + DELIMITADOR)
    except ConnectionError as erro:  # BrokenPipe, ConnectionReset, ConnectionAborted
        raise ConexaoEncerrada("a conexão foi encerrada durante o envio") from erro


def receber_mensagem(leitor: BinaryIO) -> dict[str, Any]:
    """Lê do `leitor` a próxima mensagem completa e devolve o dict correspondente.

    `leitor` é o objeto criado UMA vez por conexão com `sock.makefile("rb")`. A
    chamada bloqueia até chegar uma linha inteira (terminada em LF) ou até a
    conexão acabar. Mensagens que chegam em pedaços são remontadas; mensagens que
    chegam juntas são devolvidas uma por chamada.

    Levanta:
        ConexaoEncerrada: fim do fluxo sem nenhum byte pendente, ou conexão
            derrubada (reset).
        MensagemIncompleta: fim do fluxo com bytes pendentes sem a LF final.
        MensagemInvalida: a linha está completa mas não é uma mensagem válida (ver
            a classe). A conexão continua utilizável e a próxima chamada lê a
            mensagem seguinte.
    """
    try:
        # +1 para caber a LF de uma mensagem de exatamente TAMANHO_MAXIMO bytes.
        linha = leitor.readline(TAMANHO_MAXIMO + 1)
    except ConnectionError as erro:
        raise ConexaoEncerrada("a conexão foi encerrada durante a leitura") from erro

    if not linha:
        raise ConexaoEncerrada("a outra ponta encerrou a conexão")

    if not linha.endswith(DELIMITADOR):
        if len(linha) > TAMANHO_MAXIMO:
            _descartar_resto_da_linha(leitor)
            raise MensagemInvalida(f"mensagem excede o máximo de {TAMANHO_MAXIMO} bytes")
        raise MensagemIncompleta(
            f"a conexão terminou no meio de uma mensagem ({len(linha)} bytes sem LF)"
        )

    try:
        mensagem = json.loads(
            linha[: -len(DELIMITADOR)].decode(CODIFICACAO),
            parse_constant=_constante_invalida,
        )
    except (ValueError, RecursionError) as erro:  # inclui UnicodeDecodeError e JSONDecodeError
        raise MensagemInvalida(f"JSON inválido: {erro}") from erro

    _validar_envelope(mensagem)
    return mensagem


def _descartar_resto_da_linha(leitor: BinaryIO) -> None:
    """Consome até a próxima LF para manter o leitor alinhado após uma linha longa demais."""
    try:
        while True:
            parte = leitor.readline(TAMANHO_MAXIMO)
            if not parte or parte.endswith(DELIMITADOR):
                return
    except ConnectionError:
        return  # a próxima leitura vai informar ConexaoEncerrada
