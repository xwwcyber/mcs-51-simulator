ORG 0000H

start:
    MOV DPTR,#message

send_loop:
    CLR A
    MOVC A,@A+DPTR
    JZ done
    MOV SBUF,A

wait_ti:
    JNB TI,wait_ti
    CLR TI
    INC DPTR
    SJMP send_loop

done:
    SJMP done

message:
    DB "HELLO 51",00H

END
