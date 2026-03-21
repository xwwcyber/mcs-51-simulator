ORG 0000H
    LJMP start

INCLUDE "lib_uart.inc"

start:
    LCALL send_banner
    PRINT_CHAR 0DH
    PRINT_CHAR 0AH
    SJMP $
END
