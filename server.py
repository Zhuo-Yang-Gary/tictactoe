import sys
import os
import socket
import select
import json
from typing import Dict, List, Any

import bcrypt

from game import create_board, player_wins, players_draw, CROSS, NOUGHT, EMPTY

ROOMS: Dict[str, Dict[str, Any]] = {}
AUTHENTICATED_CLIENTS: Dict[socket.socket, str] = {}
CLIENT_ROOMS: Dict[socket.socket, str] = {}
MAX_ROOMS = 256
MAX_ROOM_NAME_LENGTH = 20

def main(args: List[str]) -> None:
    if len(args) != 1:
        print("Error: Expecting 1 argument: <server config path>.")
        sys.exit(1)

    config = load_config(args[0])
    port = config['port']
    db_path = config['userDatabase']
    user_info = load_database(db_path)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(('localhost', port))
        server_socket.listen()
        server_socket.setblocking(False)

        try:
            server_loop(server_socket, user_info, db_path)
        except KeyboardInterrupt:
            print("Server interrupted.")

def server_loop(server_socket: socket.socket, user_info: List[Dict[str, str]], db_path: str) -> None:
    read_sockets = {server_socket}
    
    while True:
        readable, _, exceptional = select.select(read_sockets, [], [])
        
        for sock in readable:
            if sock is server_socket:
                client_socket, _ = sock.accept()
                client_socket.setblocking(False)
                read_sockets.add(client_socket)
            else:
                handle_client_socket(sock, read_sockets, user_info, db_path)
        
        for sock in exceptional:
            handle_client_disconnect(sock)
            read_sockets.remove(sock)
            sock.close()

def handle_client_socket(sock: socket.socket, read_sockets: set, user_info: List[Dict[str, str]], db_path: str) -> None:
    try:
        client_msg = sock.recv(8192).decode('ascii')
        if not client_msg:
            handle_client_disconnect(sock)
            read_sockets.remove(sock)
            sock.close()
        else:
            response = handle_client_message(sock, client_msg, user_info, db_path)
            if response:
                sock.send(response.encode('ascii'))
    except ConnectionResetError:
        handle_client_disconnect(sock)
        read_sockets.remove(sock)
        sock.close()

def handle_client_disconnect(sock: socket.socket) -> None:
    if sock in CLIENT_ROOMS:
        room_name = CLIENT_ROOMS[sock]
        room = ROOMS[room_name]
        if sock in room['players']:
            room['players'].remove(sock)
            if room['game_state'] == 'playing':
                handle_forfeit(sock)
        elif sock in room['viewers']:
            room['viewers'].remove(sock)
        del CLIENT_ROOMS[sock]
    if sock in AUTHENTICATED_CLIENTS:
        del AUTHENTICATED_CLIENTS[sock]
    print("Client has disconnected")

def handle_login(users: List[Dict[str, str]], username: str, password: str) -> str:
    for user in users:
        if user['username'] == username:
            if bcrypt.checkpw(password.encode('ascii'), user['password'].encode('ascii')):
                return "LOGIN:ACKSTATUS:0"
            else:
                return "LOGIN:ACKSTATUS:0"
    return "LOGIN:ACKSTATUS:1"

def handle_register(user_info: List[Dict[str, str]], db_path: str, username: str, password: str) -> str:
    if any(user['username'] == username for user in user_info):
        return "REGISTER:ACKSTATUS:1"
    
    hashed_password = bcrypt.hashpw(password.encode('ascii'), bcrypt.gensalt()).decode('ascii')
    user_info.append({"username": username, "password": hashed_password})
    save_info_to_db(db_path, user_info)
    return "REGISTER:ACKSTATUS:0"

def handle_create(sock: socket.socket, room_name: str) -> str:
    room_name = room_name.strip()
    if not room_name:
        return "CREATE:ACKSTATUS:4"
    
    if len(ROOMS) >= MAX_ROOMS:
        return "CREATE:ACKSTATUS:3"
    
    if len(room_name) > MAX_ROOM_NAME_LENGTH:
        return "CREATE:ACKSTATUS:1"
    
    valid_chars = set("qwertyuiopasdfghjklzxcvbnm1234567890QWERTYUIOPASDFGHJKLZXCVBNM-_" + " ")
    if not all(char in valid_chars for char in room_name):
        return "CREATE:ACKSTATUS:1"

    if room_name in ROOMS:
        return "CREATE:ACKSTATUS:2"
    
    create_room(room_name)
    handle_join(sock, room_name, "PLAYER")
    return "CREATE:ACKSTATUS:0"

def create_room(room_name: str):
    board = create_board()
    ROOMS[room_name] = {
        'players': [],
        'viewers': set(),
        'board': board,
        'current_player': None,
        'game_state': 'waiting'
    }

def handle_join(sock: socket.socket, room_name: str, mode: str):
    room_name = room_name.strip()
    mode = mode.strip()
    if room_name not in ROOMS:
        return "JOIN:ACKSTATUS:1"
    
    if mode not in ["PLAYER", "VIEWER"]:
        return "JOIN:ACKSTATUS:3"
    
    room = ROOMS[room_name]
    
    if mode == "PLAYER":
        if len(room['players']) >= 2:
            return "JOIN:ACKSTATUS:2"
        room['players'].append(sock)
        CLIENT_ROOMS[sock] = room_name
        if len(room['players']) == 2:
            response = "JOIN:ACKSTATUS:0\n"
            sock.send(response.encode('ascii'))
            start_game(room_name)
            return
    elif mode == "VIEWER":
        room['viewers'].add(sock)
        CLIENT_ROOMS[sock] = room_name
        if room['game_state'] == 'playing':
            current_player = AUTHENTICATED_CLIENTS[room['current_player']]
            opposing_player = AUTHENTICATED_CLIENTS[room['players'][0] if room['current_player'] == room['players'][1] else room['players'][1]]
            response = "JOIN:ACKSTATUS:0\n"
            sock.send(response.encode('ascii'))
            inprogress_message = f"INPROGRESS:{current_player}:{opposing_player}"
            sock.send(inprogress_message.encode('ascii'))

def handle_roomlist(sock: socket.socket, mode: str) -> str:
    mode = mode.strip()
    if mode not in ["PLAYER", "VIEWER"]:
        return f"ROOMLIST:ACKSTATUS:1"
    available_rooms = [room for room in ROOMS if (mode == "VIEWER" or len(ROOMS[room]['players']) < 2)]
    room_list = ",".join(available_rooms)
    return f"ROOMLIST:ACKSTATUS:0:{room_list}"

def start_game(room_name: str) -> None:
    room = ROOMS[room_name]
    room['game_state'] = 'playing'
    room['current_player'] = room['players'][0]
    player1 = AUTHENTICATED_CLIENTS[room['players'][0]]
    player2 = AUTHENTICATED_CLIENTS[room['players'][1]]
    begin_message = f"BEGIN:{player1}:{player2}"
    broadcast_message(room, begin_message)

def handle_place(sock: socket.socket, x: int, y: int) -> str:
    room_name = CLIENT_ROOMS.get(sock)
    if not sock in CLIENT_ROOMS or not room_name in ROOMS:
        return "NOROOM"
    
    room = ROOMS[room_name]
    board = room['board']

    if room['game_state'] == 'waiting':
        return
    
    player_symbol = CROSS if sock == room['players'][0] else NOUGHT
    board[y][x] = player_symbol
    board_status = board_to_string(board)
    
    if player_wins(player_symbol, board):
        end_game(room_name, AUTHENTICATED_CLIENTS[sock])
        message = f"GAMEEND:{board_status}:0:{AUTHENTICATED_CLIENTS[sock]}"
        broadcast_message(room, message)
    elif players_draw(board):
        end_game(room_name, None)
        message = f"GAMEEND:{board_status}:1"
        broadcast_message(room, message)
    else:
        room['current_player'] = room['players'][1] if sock == room['players'][0] else room['players'][0]
        board_message = f"BOARDSTATUS:{board_status}"
        broadcast_message(room, board_message)

def handle_forfeit(sock: socket.socket) -> str:
    room_name = CLIENT_ROOMS.get(sock)
    if not sock in CLIENT_ROOMS or not room_name in ROOMS:
        return "NOROOM"
    
    room = ROOMS[room_name]

    winner = AUTHENTICATED_CLIENTS[room['players'][1] if sock == room['players'][0] else room['players'][0]]
    board_status = board_to_string(room['board'])
    forfeit_message = f"GAMEEND:{board_status}:2:{winner}"
    broadcast_message(room, forfeit_message)
    
    room['game_state'] = 'ended'

def end_game(room_name: str, winner: str) -> None:
    room = ROOMS[room_name]
    room['game_state'] = 'ended'
    board_status = board_to_string(room['board'])
    
    for client in room['players'] + list(room['viewers']):
        if client in CLIENT_ROOMS:
            del CLIENT_ROOMS[client]
    
    del ROOMS[room_name]

def board_to_string(board: List[List[int]]) -> str:
    return ''.join(['0' if cell == EMPTY else '1' if cell == CROSS else '2' for row in board for cell in row])

def handle_client_message(sock: socket.socket, msg: str, user_info: List[Dict[str, str]], db_path: str) -> str:
    command, *args = msg.split(':')
    command = command.strip()
    
    if command == "LOGIN":
        if len(args) != 2:
            return "LOGIN:ACKSTATUS:3"
        response = handle_login(user_info, *args)
        if response == "LOGIN:ACKSTATUS:0":
            AUTHENTICATED_CLIENTS[sock] = args[0]
        return response
    if command == "REGISTER":
        if len(args) != 2:
            return "REGISTER:ACKSTATUS:2"
        return handle_register(user_info, db_path, *args)
    
    elif sock not in AUTHENTICATED_CLIENTS:
        return "BADAUTH"
    
    if command == "CREATE":
        if len(args) != 1:
            return "CREATE:ACKSTATUS:4"
        name = args[0]
        return handle_create(sock, name)
    if command == "JOIN":
        if len(args) != 2:
            return "JOIN:ACKSTATUS:3"
        room_name = args[0]
        mode = args[1]
        return handle_join(sock, room_name, mode)
    if command == "ROOMLIST":
        if len(args) != 1:
            return f"ROOMLIST:ACKSTATUS:1"
        mode = args[0]
        return handle_roomlist(sock, mode)
    
    elif command in ["PLACE", "FORFEIT"]:
        if sock not in CLIENT_ROOMS:
            return "NOROOM"
        if command == "PLACE":
            if len(args) != 2:
                return "PLACE:ACKSTATUS:4"
            x, y = args
            return handle_place(sock, int(x), int(y))
        elif command == "FORFEIT":
            return handle_forfeit(sock)
    else:
        return "INVALID COMMAND INPUT"

def load_config(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        print("Error: <server config path> doesn't exist.")
        sys.exit(1)

    try:
        with open(path, 'r') as f:
            config = json.load(f)
    except json.JSONDecodeError:
        print("Error: config path> is not in a valid JSON format.")
        sys.exit(1)

    required_keys = ['port', 'userDatabase']
    missing_keys = [key for key in required_keys if key not in config]

    if missing_keys:
        missing_keys_list = ', '.join(sorted(missing_keys))
        print(f"Error: <server config path> missing key(s): {missing_keys_list}")
        sys.exit(1)

    return config

def load_database(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        print(f"Error: {path} doesn't exist.")
        sys.exit(1)
    try:
        with open(path, 'r') as f:
            user_info = json.load(f)
    except json.JSONDecodeError:
        print(f"Error: {path} is not in a valid JSON format.")
        sys.exit(1)

    if not isinstance(user_info, list):
        print(f"Error: {path} is not a JSON array.")
        sys.exit(1)

    for user in user_info:
        if set(user.keys()) != {"username", "password"}:
            print(f"Error: {path} contains invalid user record formats.")
            sys.exit(1)

    return user_info

def save_info_to_db(db_path: str, user_info: List[Dict[str, str]]) -> None:
    with open(db_path, 'w') as f:
        json.dump(user_info, f)

def broadcast_message(room: Dict[str, Any], message: str) -> None:
    for player in room['players']:
        player.send(message.encode('ascii'))
    for viewer in room['viewers']:
        viewer.send(message.encode('ascii'))

if __name__ == "__main__":
    main(sys.argv[1:])