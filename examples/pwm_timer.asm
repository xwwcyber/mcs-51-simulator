        ORG 0000H
        LJMP start

        ORG 001BH               ; Timer 1 ISR vector
timer1_isr:
        CPL P1.0                ; toggle PWM output pin
        INC 30H                 ; increment toggle count
        RETI

        ORG 0030H
start:
        MOV 30H,#00H            ; clear toggle counter
        MOV TMOD,#20H           ; Timer 1 Mode 2 (8-bit auto-reload)
        MOV TH1,#0E0H           ; reload = 224, period = 32 ticks
        MOV TL1,#0E0H
        MOV IE,#88H             ; EA=1, ET1=1
        SETB TR1                ; start Timer 1

main_loop:
        MOV A,30H
        CJNE A,#10,main_loop    ; run until 10 toggles
        CLR TR1                 ; stop timer

done:
        SJMP done

        END
