// Decompile every function to one C file, plus an index of where they live.
//
// Grepping that dump is how we locate a chunk loader: the four-character chunk ids
// appear in the code as 32-bit immediates, so searching for those constants leads
// straight to the function that parses the chunk.
//
//@category Croc2
import java.io.PrintWriter;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;

public class ExportDecompiled extends GhidraScript {

    private static final int TIMEOUT_SECONDS = 60;

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        Path outDir = Paths.get(args.length > 0 ? args[0] : "/out");
        Files.createDirectories(outDir);

        DecompInterface decompiler = new DecompInterface();
        decompiler.setOptions(new DecompileOptions());
        decompiler.toggleCCode(true);
        if (!decompiler.openProgram(currentProgram)) {
            printerr("could not open program: " + decompiler.getLastMessage());
            return;
        }

        Path cPath = outDir.resolve("decompiled.c");
        Path indexPath = outDir.resolve("functions.csv");
        int done = 0, failed = 0;

        try (PrintWriter code = new PrintWriter(Files.newBufferedWriter(cPath));
             PrintWriter index = new PrintWriter(Files.newBufferedWriter(indexPath))) {

            code.println("// " + currentProgram.getName()
                         + " decompiled by Ghidra, one function per block");
            index.println("address,name,size,calls_out");

            FunctionIterator functions =
                currentProgram.getFunctionManager().getFunctions(true);
            for (Function f : functions) {
                if (monitor.isCancelled()) {
                    break;
                }
                monitor.setMessage("decompiling " + f.getName());

                index.printf("%s,%s,%d,%d%n", f.getEntryPoint(), f.getName(),
                             f.getBody().getNumAddresses(),
                             f.getCalledFunctions(monitor).size());

                DecompileResults r =
                    decompiler.decompileFunction(f, TIMEOUT_SECONDS, monitor);
                code.println();
                code.println("// ==== " + f.getName() + " @ " + f.getEntryPoint()
                             + " ====");
                if (r != null && r.decompileCompleted()
                        && r.getDecompiledFunction() != null) {
                    code.println(r.getDecompiledFunction().getC());
                    done++;
                } else {
                    String why = (r == null) ? "no result" : r.getErrorMessage();
                    code.println("// decompilation failed: " + why);
                    failed++;
                }
            }
        } finally {
            decompiler.dispose();
        }

        println("decompiled " + done + " functions, " + failed + " failed");
        println("wrote " + cPath + " and " + indexPath);
    }
}
