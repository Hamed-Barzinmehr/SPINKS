#!/bin/bash
#PBS -N Nickel_INT2_Engrad_doublet
#PBS -l nodes=1:ppn=12
#PBS -o Nickel_INT2_Engrad_doublet.pbs.out
#PBS -e Nickel_INT2_Engrad_doublet.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Nickel_INT2_Engrad_doublet"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Nickel_INT2_Engrad_doublet.inp > Nickel_INT2_Engrad_doublet.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
