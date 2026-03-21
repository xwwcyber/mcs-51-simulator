        ORG 0000H
        LJMP start

        ORG 000BH               ; Timer 0 ISR vector
timer0_isr:
        PUSH ACC
        PUSH DPL
        PUSH DPH
        MOV DPTR,#msg_done
        LCALL send_string
        CLR TR0                 ; stop counter
        POP DPH
        POP DPL
        POP ACC
        RETI

        ORG 0030H
start:
        ; Timer 0 Mode 2 (8-bit auto-reload), C/T=1 (external counter)
        ; TMOD[3:0]: GATE=0, C/T=1, M1=1, M0=0 = 0x06
        MOV TMOD,#06H
        MOV TH0,#0F6H           ; reload = 246, overflow after 10 pulses
        MOV TL0,#0F6H
        MOV IE,#82H             ; EA=1, ET0=1
        SETB TR0                ; start counter

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
msg_done:
        DB "COUNT OK\r\n",00H

        END
