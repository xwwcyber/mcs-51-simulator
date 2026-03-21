ORG 0000H
    LJMP start

ORG 000BH
timer0_isr:
    INC 30H
    CPL P1.0
    RETI

ORG 0030H
start:
    MOV TMOD,#02H
    MOV TH0,#0FCH
    MOV TL0,#0FCH
    MOV IE,#82H
    SETB TR0
    MOV SCON,#10H

    MOV DPTR,#prompt
    LCALL send_string

main_loop:
    JNB RI,main_loop
    MOV A,SBUF
    CLR RI
    CJNE A,#0DH,echo_char

    MOV DPTR,#newline
    LCALL send_string
    SJMP main_loop

echo_char:
    MOV SBUF,A

wait_ti:
    JNB TI,wait_ti
    CLR TI
    SJMP main_loop

send_string:
    CLR A
    MOVC A,@A+DPTR
    JZ send_done
    MOV SBUF,A

send_wait_ti:
    JNB TI,send_wait_ti
    CLR TI
    INC DPTR
    SJMP send_string

send_done:
    RET

ORG 0080H
prompt:
    DB "READY>\r\n",00H

newline:
    DB "\r\n",00H

END
