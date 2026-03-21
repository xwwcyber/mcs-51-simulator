PRINT_CHAR MACRO value
    MOV A,#value
    MOV SBUF,A
ENDM

send_banner:
    MOV DPTR,#banner
send_banner_loop:
    CLR A
    MOVC A,@A+DPTR
    JZ send_banner_done
    MOV SBUF,A
send_banner_wait:
    JNB TI,send_banner_wait
    CLR TI
    INC DPTR
    SJMP send_banner_loop
send_banner_done:
    RET

banner:
    DB "INCLUDE+MACRO",00H
