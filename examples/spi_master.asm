; SPI master example: bit-bang SPI on P1
; CS=P1.7 (0x97), SCK=P1.4 (0x94), MOSI=P1.5 (0x95), MISO=P1.6 (0x96)
; Sends 0xAB, reads 1 byte back, sends received byte to UART

CS      EQU 97H
SCK     EQU 94H
MOSI    EQU 95H
MISO    EQU 96H

ORG 0000H
    SJMP main

ORG 0030H
main:
    SETB CS
    CLR  SCK
    SETB MOSI

    ; Assert CS (active low)
    CLR CS

    ; Send 0xAB and receive 1 byte simultaneously
    MOV A,#0ABH
    ACALL spi_transfer    ; result (MISO byte) in A

    ; Deassert CS
    SETB CS

    ; Send received byte to UART
    MOV SBUF,A
wait_ti:
    JNB TI,wait_ti
    CLR TI

done:
    SJMP done

; --- spi_transfer: send byte in A (MSB first), return received byte in A ---
; Mode 0: CPOL=0, CPHA=0 — sample on rising edge of SCK
spi_transfer:
    MOV R2,A           ; save TX byte
    MOV R0,#8
    MOV R1,#0          ; accumulate received bits in R1
spi_bit:
    MOV A,R2
    RLC A              ; shift out MSB into CY
    MOV R2,A
    JC  mosi_high
    CLR MOSI
    SJMP mosi_done
mosi_high:
    SETB MOSI
mosi_done:
    SETB SCK           ; rising edge: slave latches MOSI, drives MISO
    MOV C,MISO         ; sample MISO
    MOV A,R1
    RLC A              ; shift received bit into R1
    MOV R1,A
    CLR SCK            ; falling edge
    DJNZ R0,spi_bit
    MOV A,R1
    RET

END
