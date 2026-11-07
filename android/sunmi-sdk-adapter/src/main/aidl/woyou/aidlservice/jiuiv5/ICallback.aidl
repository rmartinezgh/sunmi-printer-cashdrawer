package woyou.aidlservice.jiuiv5;

/**
 * SUNMI Woyou Printer & Cash Drawer Service Callback Interface.
 * Invoked asynchronously by the system printer service upon command dispatch or completion.
 */
interface ICallback {
    /**
     * Called when the command has been accepted or processed by the driver.
     * Note: For openDrawer(), isSuccess=true only confirms electrical pulse dispatch,
     * NOT physical drawer movement.
     */
    oneway void onRunResult(boolean isSuccess);

    /**
     * Returns string result data from a query command.
     */
    oneway void onReturnString(String result);

    /**
     * Called when the printer or drawer subsystem encounters an exception.
     */
    oneway void onRaiseException(int code, String msg);

    /**
     * Called when a print job with a transaction/commit ID completes.
     */
    oneway void onPrintResult(int code, String msg);
}
