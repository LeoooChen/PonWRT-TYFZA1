/* SPDX-License-Identifier: GPL-2.0-only */
/* Read/write a TYFZA1 radio's PCI BAR2 for staged original-register checks. */
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
int main(int argc,char **argv)
{
 char path[160]; unsigned long off; int fd; volatile uint32_t *bar;
 if(argc!=3 && argc!=4){fprintf(stderr,"usage: radio_register 0000:01:00.0|0001:01:00.0 offset [value]\n");return 1;}
 if(strcmp(argv[1],"0000:01:00.0") && strcmp(argv[1],"0001:01:00.0"))return 1;
 off=strtoul(argv[2],NULL,0);if(off>=0x100000 || (off&3))return 1;
 snprintf(path,sizeof(path),"/sys/bus/pci/devices/%s/resource2",argv[1]);
 fd=open(path,O_RDWR|O_SYNC);if(fd<0){perror("open BAR2");return 1;}
 bar=mmap(NULL,0x100000,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
 if(bar==MAP_FAILED){perror("mmap BAR2");return 1;}
 if(argc==4)bar[off/4]=(uint32_t)strtoul(argv[3],NULL,0);
 printf("%s %05lx=%08x\n",argv[1],off,bar[off/4]);
 munmap((void*)bar,0x100000);close(fd);return 0;
}
