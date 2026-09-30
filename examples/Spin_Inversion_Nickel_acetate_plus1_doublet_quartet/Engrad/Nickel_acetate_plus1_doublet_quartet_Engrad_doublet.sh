#!/bin/bash
#PBS -N Nickel_acetate_plus1_doublet_quartet_Engrad_doublet
#PBS -l nodes=1:ppn=12
#PBS -o Nickel_acetate_plus1_doublet_quartet_Engrad_doublet.pbs.out
#PBS -e Nickel_acetate_plus1_doublet_quartet_Engrad_doublet.pbs.err

cd $PBS_O_WORKDIR

echo "===================================================================="
echo "Job name: Nickel_acetate_plus1_doublet_quartet_Engrad_doublet"
echo "Working directory:"
pwd
echo "Start time:"
date
echo "===================================================================="

module load openmpi/4.1.5
export PATH=/usr/local/apps/orca/5.0.4:$PATH

/usr/local/apps/orca/5.0.4/orca Nickel_acetate_plus1_doublet_quartet_Engrad_doublet.inp > Nickel_acetate_plus1_doublet_quartet_Engrad_doublet.out

echo "===================================================================="
echo "End time:"
date
echo "===================================================================="
