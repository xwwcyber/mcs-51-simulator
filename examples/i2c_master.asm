; I2C master example: bit-bang I2C on P3.6 (SCL) and P3.7 (SDA)
; Writes 0xAB to slave address 0x27, then reads 1 byte back
; Sends the received byte to UART
;
; Pin mapping: SCL=P3.6 (bit addr 0xB6), SDA=P3.7 (bit addr 0xB7)

SCL     EQU 0B6H
SDA     EQU 0B7H

ORG 0000H
    SJMP main

ORG 0030H
main:
    SETB SCL
    SETB SDA

    ; === Write transaction: START, addr 0x27 W, data 0xAB, STOP ===
    ACALL i2c_start

    MOV A,#4EH          ; 0x27 << 1 | 0 = 0x4E (write)
    ACALL i2c_send_byte

    MOV A,#0ABH
    ACALL i2c_send_byte

    ACALL i2c_stop

    ; === Read transaction: START, addr 0x27 R, read 1 byte, NAK, STOP ===
    ACALL i2c_start

    MOV A,#4FH          ; 0x27 << 1 | 1 = 0x4F (read)
    ACALL i2c_send_byte

    ACALL i2c_recv_byte ; result in A

    ACALL i2c_send_nak

    ACALL i2c_stop

    ; Send received byte to UART
    MOV SBUF,A
wait_ti:
    JNB TI,wait_ti
    CLR TI

done:
    SJMP done

; --- i2c_start: generate START condition (SDA falling while SCL high) ---
i2c_start:
    SETB SDA
    SETB SCL
    CLR SDA
    CLR SCL
    RET

; --- i2c_stop: generate STOP condition (SDA rising while SCL high) ---
i2c_stop:
    CLR SDA
    SETB SCL
    SETB SDA
    RET

; --- i2c_send_byte: send byte in A, MSB first; discard ACK ---
i2c_send_byte:
    MOV R0,#8
send_bit:
    RLC A
    JC  sda_high
    CLR SDA
    SJMP sda_done
sda_high:
    SETB SDA
sda_done:
    SETB SCL
    CLR  SCL
    DJNZ R0,send_bit
    ; clock in ACK bit (release SDA, pulse SCL)
    SETB SDA
    SETB SCL
    CLR  SCL
    RET

; --- i2c_recv_byte: receive byte into A, MSB first ---
i2c_recv_byte:
    MOV R0,#8
    MOV A,#0
recv_bit:
    SETB SDA            ; release SDA so slave can drive
    SETB SCL
    MOV C,SDA
    RLC A
    CLR SCL
    DJNZ R0,recv_bit
    RET

; --- i2c_send_nak: send NAK (SDA=1 during ACK clock) ---
i2c_send_nak:
    SETB SDA
    SETB SCL
    CLR  SCL
    RET

END
