import sys
import socket
import select
from typing import List, Optional
from game import create_board, print_board, CROSS, NOUGHT, EMPTY


current_board = create_board()
is_player = False
username = ""
current_room = ""
need_wait = False
player1 = ""
player2 = ""

def handle_login(sock: socket.socket) -> bool:

    global username

    username = input("Enter username: ")
    password = input("Enter password: ")
    message = f"LOGIN:{username}:{password}"
    response = send_message(sock, message)

    if response == "LOGIN:ACKSTATUS:0":
        print(f"Welcome {username}")
        return True
    elif response == "LOGIN:ACKSTATUS:1":
        print(f"Error: User {username} not found", file=sys.stderr)
    elif response == "LOGIN:ACKSTATUS:2":
        print(f"Error: Wrong password for user {username}", file=sys.stderr)
    return False

def handle_register(sock: socket.socket) -> None:

    username = input("Enter username: ")
    password = input("Enter password: ")

    message = f"REGISTER:{username}:{password}"
    response = send_message(sock, message)
    
    #process registration response
    if response == "REGISTER:ACKSTATUS:0":
        print(f"Successfully created user account {username}")
    elif response == "REGISTER:ACKSTATUS:1":
        print(f"Error: User {username} already exists",file=sys.stderr)

def handle_roomlist(sock: socket.socket) -> Optional[str]:
    mode = input("Do you want to have a room list as player or viewer? (Player/Viewer): ").upper()
    is_player = (mode == "PLAYER")

    message = f"ROOMLIST:{mode}"
    response = send_message(sock, message)

    
    if response == "BADAUTH":
        return "BADAUTH"
    args_response = response.split(':')
    if len(args_response) >= 3 and args_response[2] == '0':
        print(f"Room available to join as {mode}: {args_response[3] if len(args_response) > 3 else 'No rooms available'}")
    elif len(args_response) >= 3 and args_response[2] == '1':
        print("Error: Please input a valid mode.")
    else:
        print("Unexpected response from server.")
    return None
    

def handle_create(sock: socket.socket) -> None:
    global current_room, need_wait

    room_name = input("Enter room name you want to create: ")

    message = f"CREATE:{room_name}"

    response = send_message(sock, message)
    if response == "BADAUTH":
        return response

    if response == "CREATE:ACKSTATUS:0":
        print(f"Successfully created room {room_name}")
        current_room = room_name
        print("Waiting for other player...")
        need_wait = True
    elif response == "CREATE:ACKSTATUS:1":
        print(f"Error: Room {room_name} is invalid", file=sys.stderr)
    elif response == "CREATE:ACKSTATUS:2":
        print(f"Error: Room {room_name} already exists", file=sys.stderr)
    elif response == "CREATE:ACKSTATUS:3":
        print("Error: Server already contains a maximum of 256 rooms", file=sys.stderr)

def handle_join(sock: socket.socket, room_name: Optional[str] = None, mode: Optional[str] = None) -> None:
    global current_room, username, is_player, opposing_player, current_turn
    room_name = input("Enter room name you want to join: ")
    mode = input("You wish to join the room as: (Player/Viewer): ").upper()

    is_player = (mode == "PLAYER")

    if room_name == None or mode == None:
        return "JOIN:ACKSTATUS:3"
    message = f"JOIN:{room_name}:{mode}"
    #send join room request and get response
    response = send_message(sock, message)
    if response == "BADAUTH":
        return response

    #process join room response
    if response == "JOIN:ACKSTATUS:0":
        print(f"Successfully joined room {room_name} as a {mode}")
        current_room = room_name
        is_player = (mode == "PLAYER")
        if is_player:
            #wait for BEGIN message to set opposing_player and current_turn
            begin_message = sock.recv(8192).decode('ascii')
            if begin_message.startswith("BEGIN:"):
                _, player1, player2 = begin_message.split(":")
                opposing_player = player2 if username == player1 else player1
                current_turn = player1
    elif response == "JOIN:ACKSTATUS:1":
        print(f"Error: No room named {room_name}", file=sys.stderr)
    elif response == "JOIN:ACKSTATUS:2":
        print(f"Error: The room {room_name} already has 2 players", file=sys.stderr)

def handle_place(sock: socket.socket) -> None:

    global current_turn, opposing_player
    while True:
        x = int(input("Enter column (0-2): "))
        y = int(input("Enter row (0-2): "))
        if 0 <= x <= 2 and 0 <= y <= 2:
            if current_board[y][x] == EMPTY:
                message = f"PLACE:{x}:{y}"
                response = send_message(sock, message)
                if response.startswith("BOARDSTATUS:"):
                    current_turn = opposing_player
                    handle_boardstatus(sock, response.split(":")[1])
                elif response.startswith("GAMEEND:"):
                    handle_gameend(sock, response)
                break
            else:
                print(f"({x}, {y}) is already occupied.")
        else:
            print("(Column/Row) values must be an integer between 0 and 2")


opposing_player = ""
current_turn = ""

def handle_forfeit(sock: socket.socket) -> None:
    global current_room
    message = "FORFEIT"

    response = send_message(sock, message)
    handle_gameend(sock, response)

def handle_boardstatus(sock: socket.socket, board_status: str) -> None:
    global current_board, is_player, username, current_turn, player1, player2

    for i, char in enumerate(board_status):
        row = i // 3
        col = i % 3
        if char == '0':
            current_board[row][col] = EMPTY
        elif char == '1':
            current_board[row][col] = CROSS
        elif char == '2':
            current_board[row][col] = NOUGHT

    print_board(current_board)

    current_turn = player2 if current_turn == player1 else player1

    if is_player:
        if current_turn == username:
            print("It is your turn")
            handle_place(sock)
        else:
            print(f"It is {current_turn}'s turn")
    else:
        print(f"It is {current_turn}'s turn")

def handle_gameend(sock: socket.socket, response: str) -> None:
    global current_room, username, is_player
    parts = response.split(":")
    board_status = parts[1]
    result = parts[2]
    winner = parts[3] if len(parts) > 3 else None

    handle_boardstatus(sock, board_status)

    if is_player:
        if result == "0":
            if winner == username:
                print("Congratulations, you won!")
            else:
                print(f"Sorry you lost. Good luck next time.")
        elif result == "1":
            print("Game ended in a draw.")
        elif result == "2":
            if winner == username:
                print(f"You won due to the opposing player forfeiting.")
            else:
                print(f"{winner} won due to the opposing player forfeiting.")
    else:  # Viewer
        if result == "0":
            print(f"{winner} has won this game.")
        elif result == "1":
            print("Game ended in a draw.")
        elif result == "2":
            print(f"{winner} won due to the opposing player forfeiting.")
    
    current_room = ""
    is_player = False

def send_message(sock: socket.socket, message: str) -> Optional[str]:
    sock.sendall(message.encode('ascii'))
    return sock.recv(8192).decode('ascii')

def handle_user_input(sock: socket.socket) -> None:
    global need_wait
    while True:
        ready_to_read, _, _ = select.select([sock], [], [], 0.1)
        if ready_to_read:
            message = sock.recv(8192).decode('ascii')
            if message:
                handle_all_message(sock, message)

        if not need_wait:
            try:
                command = input("Enter command: ").upper()
                if command == "QUIT":
                    print("Exiting...")
                    return
                elif command == "LOGIN":
                    if handle_login(sock):
                        continue
                elif command == "REGISTER":
                    handle_register(sock)
                elif command == "ROOMLIST":
                    if handle_roomlist(sock) == "BADAUTH":
                        handle_all_message(sock, "BADAUTH")
                        continue
                elif command == "CREATE":
                    if handle_create(sock) == "BADAUTH":
                        handle_all_message(sock, "BADAUTH")
                        continue
                elif command == "JOIN":
                    if handle_join(sock) == "BADAUTH":
                        handle_all_message(sock, "BADAUTH")
                        continue
                elif command == "PLACE":
                    handle_place(sock)
                elif command == "FORFEIT":
                    handle_forfeit(sock)
                else:
                    print(f"Unknown command: {command}")
            except EOFError:
                print("\nDebug: EOF detected, exiting gracefully")
                break
    
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    sock.close()

def handle_all_message(sock, message):
    global need_wait, player1, player2, current_turn, username, is_player

    if message.startswith("BOARDSTATUS"):
        command, board_status = message.split(":")
        handle_boardstatus(sock, board_status)
    elif message.startswith("BEGIN"):
        _, player1, player2 = message.split(":")
        current_turn = player1
        print(f"match between {player1} and {player2} will commence, it is currently {player1}'s turn.")
        
        if is_player:
            if username == player1:
                print("It's your turn. Place your first marker.")
                handle_place(sock)
            else:
                print(f"Waiting for {player1} to place their first marker.")
        need_wait = False
    elif message.startswith("BADAUTH"):
        print("Error: You must be logged in to perform this action.")

def main(args: List[str]):
    if len(args) != 2:
        print("Error: Expecting 2 arguments: <server address> <port>")
        sys.exit(1)

    server_address = args[0]
    port = int(args[1])

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((server_address, port))
            handle_user_input(s)
            s.close()
    except ConnectionRefusedError:
        print(f"Error: cannot connect to server at {server_address} and {port}.")
        sys.exit(1)

if __name__ == "__main__":
    main(sys.argv[1:])