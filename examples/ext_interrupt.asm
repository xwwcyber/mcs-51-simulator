        ORG 0000H
        LJMP start

        ORG 0003H               ; INT0 ISR vector
ext0_isr:
        PUSH ACC
        PUSH DPL
        PUSH DPH
        MOV DPTR,#msg_int0
        LCALL send_string
        POP DPH
        POP DPL
        POP ACC
        RETI

        ORG 0030H
start:
        SETB IT0                ; INT0 edge-triggered mode
        MOV IE,#81H             ; EA=1, EX0=1

main_loop:
        SJMP main_loop

; Send null-terminated string pointed to by DPTR via serial
send_string:
        CLR A
        MOVC A,@A+DPTR
        JZ send_done
        MOV SBUF,A
wait_ti:
        JNB TI,wait_ti
        CLR TI
        INC DPTR
        SJMP send_string
send_done:
        RET

        ORG 0080H
msg_int0:
        DB "INT0!\r\n",00H

        END
