import {fireEvent,screen} from '@testing-library/react';

/** Open the real listbox and select its option, rather than changing a hidden input. */
export function chooseSelect(control:HTMLElement,value:string){
 if(!HTMLElement.prototype.scrollIntoView)HTMLElement.prototype.scrollIntoView=()=>{};
 if(!globalThis.ResizeObserver)globalThis.ResizeObserver=class {observe(){}unobserve(){}disconnect(){}};
 fireEvent.keyDown(control,{key:'ArrowDown'});
 const option=screen.getAllByRole('option').find(row=>row.getAttribute('data-select-value')===value);
 if(!option)throw new Error(`Select option ${value} is unavailable`);
 fireEvent.click(option);
}
