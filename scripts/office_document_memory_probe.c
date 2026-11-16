/* Fixed trusted diagnostic, not a parser or production memory boundary. */
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <unistd.h>

int main(int argc, char **argv) {
    const size_t ceiling = 32u * 1024u * 1024u;
    const size_t allocation = 48u * 1024u * 1024u;
    if (argc != 2) return 2;
    bool baseline = strcmp(argv[1], "baseline") == 0;
    int resource;
    if (baseline || strcmp(argv[1], "as") == 0) resource = RLIMIT_AS;
    else if (strcmp(argv[1], "data") == 0) resource = RLIMIT_DATA;
    else return 2;
    bool installed = false;
    int install_errno = 0;
    if (!baseline) {
        struct rlimit limit = {ceiling, ceiling};
        if (setrlimit(resource, &limit) == 0) installed = true;
        else install_errno = errno;
    }
    void *memory = mmap(NULL, allocation, PROT_READ | PROT_WRITE,
                        MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    bool mapped = memory != MAP_FAILED;
    int allocation_errno = mapped ? 0 : errno;
    /* Touch one byte only. This tests mapping refusal, not resident peak RAM. */
    if (mapped) {
        ((volatile unsigned char *)memory)[0] = 1;
        if (munmap(memory, allocation) != 0) return 1;
    }
    printf("{\"case\":\"%s\",\"installed\":%s,\"install_errno\":%d,"
           "\"mapped\":%s,\"allocation_errno\":%d,\"ceiling_bytes\":%zu,"
           "\"allocation_bytes\":%zu,\"rss_aliases_as\":%s}\n", argv[1],
           installed ? "true" : "false", install_errno,
           mapped ? "true" : "false", allocation_errno, ceiling, allocation,
           RLIMIT_RSS == RLIMIT_AS ? "true" : "false");
    return 0;
}
