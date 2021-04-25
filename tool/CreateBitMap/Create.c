#include <stdio.h>  
#include <stdlib.h>  
  
int main(void)  
{  
    FILE *fp = NULL;

    fp=fopen("gb2312.txt","wb");
    if(fp==NULL)  
    {  
        perror("Cann't open gb2312.txt");  
        return -1;  
    }  
    else
    {
        printf("Creat file gb2312.txt Success\n");
    }

    for (int i = 0xB0; i <= 0xF7; ++i)
    {
        for (int j = 0xA1; j<= 0xFE; ++j) 
        {
            printf("%c%c", i, j);
            fputc(i, fp);
            fputc(j, fp);
        }
    }

    fclose(fp);  
    printf("\nDone");  
  
    return 0;  
} 